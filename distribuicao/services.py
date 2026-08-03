from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from xml.sax.saxutils import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cadastros.models import Cliente, Loja, Nat_Lancamento, PlanoContabil
from fiscal.models import NotaFiscalSaida, NotaFiscalSaidaItem
from financeiro.models import MovimentacaoFinanceira, Receber, ReceberItem, ReceberRateio
from financeiro.services import gerar_lancamento_contabil_movimentacao
from produto.models import Estoque, ProdutoDetalhe, EstoqueMovimentacao

from .models import (
    Distribuicao,
    DistribuicaoDestino,
    DistribuicaoItem,
    MercadoriaTransito,
    PedidoVendaDistribuicao,
    PedidoVendaDistribuicaoItem,
    PerfilDistribuicao,
)


def decimal_qtd(value):
    return Decimal(value or 0).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def decimal_money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def proximo_numero(model, empresa_id, prefixo):
    total = model.objects.filter(empresa_id=empresa_id).count() + 1
    return f"{prefixo}-{timezone.localdate():%Y}-{total:06d}"


def proxima_nfe(loja_origem):
    serie = str(loja_origem.serie_nfe or 1)
    proximo = int(loja_origem.proximo_numero_nfe or 1)
    usados = set(
        NotaFiscalSaida.objects
        .filter(empresa=loja_origem.empresa, modelo="55", serie=serie)
        .values_list("numero", flat=True)
    )
    while str(proximo) in usados:
        proximo += 1
    numero = str(proximo)
    loja_origem.proximo_numero_nfe = proximo + 1
    loja_origem.save(update_fields=["proximo_numero_nfe"])
    return serie, numero


def validar_loja_empresa(loja, empresa_id, field="loja"):
    if not loja or loja.empresa_id != empresa_id:
        raise ValidationError({field: "A loja informada pertence a outra empresa."})
    if not loja.ativo:
        raise ValidationError({field: "A loja informada está inativa."})


def buscar_skus_disponiveis(empresa_id, unidade_origem_id, search=""):
    qs = Estoque.objects.select_related("Idloja").filter(Idloja_id=unidade_origem_id, Idloja__empresa_id=empresa_id)
    if search:
        qs = qs.filter(Q(referencia__icontains=search) | Q(CodigodeBarra__icontains=search))

    detalhes = {
        sku.ean13: sku
        for sku in ProdutoDetalhe.objects.select_related("produto", "idcor", "idtamanho").filter(
            produto__empresa_id=empresa_id,
            produto__ativo=True,
            produto__bloqueado_venda=False,
            ativo=True,
            bloqueado_venda=False,
            ean13__in=qs.values_list("CodigodeBarra", flat=True),
        )
    }
    rows = []
    for estoque in qs:
        sku = detalhes.get(estoque.CodigodeBarra)
        if not sku:
            continue
        fisico = decimal_qtd(estoque.Estoque)
        reservado = decimal_qtd(estoque.reserva)
        disponivel = max(Decimal("0.000"), fisico - reservado)
        if disponivel <= 0:
            continue
        produto = sku.produto
        custo_unitario = sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or Decimal("0")
        rows.append({
            "estoque": estoque,
            "sku": sku,
            "produto": produto,
            "referencia": produto.referencia or estoque.referencia or "",
            "descricao": produto.descricao,
            "cor_descricao": getattr(sku.idcor, "Descricao", "") if sku.idcor_id else "",
            "tamanho_descricao": getattr(sku.idtamanho, "Tamanho", "") if sku.idtamanho_id else "",
            "ean13": sku.ean13,
            "estoque_fisico": fisico,
            "estoque_reservado": reservado,
            "estoque_disponivel": disponivel,
            "custo_unitario": custo_unitario,
        })
    return rows


def loja_central_producao(empresa):
    loja = (
        Loja.objects
        .filter(empresa=empresa, ativo=True, tipo_unidade=Loja.TIPO_FABRICA)
        .order_by("id")
        .first()
    )
    if loja:
        return loja
    loja = (
        Loja.objects
        .filter(empresa=empresa, ativo=True, tipo_unidade=Loja.TIPO_MATRIZ)
        .order_by("id")
        .first()
    )
    if loja:
        return loja
    loja = Loja.objects.filter(empresa=empresa, ativo=True, Matriz="SIM").order_by("id").first()
    if loja:
        return loja
    loja = Loja.objects.filter(empresa=empresa, ativo=True).order_by("id").first()
    if not loja:
        raise ValidationError({"loja": "Cadastre uma fábrica ou matriz/estoque central ativo para distribuir a produção."})
    return loja


@transaction.atomic
def preparar_distribuicao_producao(ordem, perfil=None, user=None):
    from produto.models import OrdemProducao

    if ordem.status != OrdemProducao.STATUS_FINALIZADA:
        raise ValidationError("Somente OP finalizada pode seguir para distribuição.")

    existente = (
        Distribuicao.objects
        .select_for_update()
        .filter(
            empresa=ordem.empresa,
            origem_operacao=Distribuicao.ORIGEM_PRODUCAO,
            origem_id=ordem.pk,
        )
        .exclude(status=Distribuicao.STATUS_CANCELADA)
        .order_by("-id")
        .first()
    )
    if existente:
        if perfil and existente.status in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
            aplicar_perfil(existente, perfil)
            existente.refresh_from_db()
        return existente

    perfil = perfil or (
        PerfilDistribuicao.objects
        .filter(empresa=ordem.empresa, ativo=True)
        .prefetch_related("itens")
        .order_by("codigo", "id")
        .first()
    )
    loja_origem = loja_central_producao(ordem.empresa)
    distribuicao = Distribuicao.objects.create(
        empresa=ordem.empresa,
        numero=proximo_numero(Distribuicao, ordem.empresa_id, "DIST"),
        unidade_origem=loja_origem,
        data=timezone.localdate(),
        perfil=perfil,
        tipo=perfil.tipo if perfil else PerfilDistribuicao.TIPO_MANUAL,
        fator_preco=perfil.fator_preco if perfil else Decimal("0.2000"),
        origem_operacao=Distribuicao.ORIGEM_PRODUCAO,
        origem_id=ordem.pk,
        observacao=f"Distribuição gerada pela OP {ordem.numero}",
        criado_por=user if getattr(user, "is_authenticated", False) else None,
    )

    estoques = {
        est.CodigodeBarra: est
        for est in Estoque.objects.filter(Idloja=loja_origem, CodigodeBarra__in=ordem.grade_producao.values_list("sku_final__ean13", flat=True))
    }
    for linha in ordem.grade_producao.select_related("sku_final", "sku_final__produto", "sku_final__idcor", "sku_final__idtamanho"):
        sku = linha.sku_final
        produto = sku.produto
        estoque = estoques.get(sku.ean13)
        qtd = decimal_qtd(linha.quantidade)
        custo_unitario = (
            sku.custo_medio
            or sku.custo_ultima_compra
            or sku.custo_original
            or produto.custo_medio
            or produto.custo_ultima_compra
            or produto.custo_original
            or (Decimal(ordem.custo_real or 0) / Decimal(ordem.quantidade or 1))
            or Decimal("0")
        )
        custo_unitario = Decimal(custo_unitario or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        DistribuicaoItem.objects.create(
            distribuicao=distribuicao,
            produto=produto,
            sku=sku,
            referencia=produto.referencia or "",
            descricao=produto.descricao,
            cor_descricao=getattr(sku.idcor, "Descricao", "") if sku.idcor_id else "",
            tamanho_descricao=getattr(sku.idtamanho, "Tamanho", "") if sku.idtamanho_id else "",
            ean13=sku.ean13,
            estoque_fisico=decimal_qtd(estoque.Estoque if estoque else 0),
            estoque_reservado=decimal_qtd(estoque.reserva if estoque else 0),
            estoque_disponivel=qtd,
            quantidade_selecionada=qtd,
            custo_unitario=custo_unitario,
            custo_total=decimal_money(qtd * custo_unitario),
        )

    if perfil:
        aplicar_perfil(distribuicao, perfil)
    else:
        montar_matriz_manual(distribuicao)
    distribuicao.refresh_from_db()
    return distribuicao


@transaction.atomic
def carregar_estoque(distribuicao, search="", quantidade=None, manter_minimo=0):
    if distribuicao.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
        raise ValidationError("Só é possível carregar estoque em distribuição em rascunho ou calculada.")
    distribuicao.destinos.all().delete()
    distribuicao.itens.all().delete()
    minimo = decimal_qtd(manter_minimo)
    limite = decimal_qtd(quantidade) if quantidade not in (None, "") else None
    criados = 0
    for row in buscar_skus_disponiveis(distribuicao.empresa_id, distribuicao.unidade_origem_id, search):
        selecionada = max(Decimal("0.000"), row["estoque_disponivel"] - minimo)
        if limite is not None:
            selecionada = min(selecionada, limite)
        if selecionada <= 0:
            continue
        custo_total = decimal_money(selecionada * row["custo_unitario"])
        DistribuicaoItem.objects.create(
            distribuicao=distribuicao,
            produto=row["produto"],
            sku=row["sku"],
            referencia=row["referencia"],
            descricao=row["descricao"],
            cor_descricao=row["cor_descricao"],
            tamanho_descricao=row["tamanho_descricao"],
            ean13=row["ean13"],
            estoque_fisico=row["estoque_fisico"],
            estoque_reservado=row["estoque_reservado"],
            estoque_disponivel=row["estoque_disponivel"],
            quantidade_selecionada=selecionada,
            custo_unitario=row["custo_unitario"],
            custo_total=custo_total,
        )
        criados += 1
    distribuicao.status = Distribuicao.STATUS_RASCUNHO
    distribuicao.recomputar_totais()
    distribuicao.save(update_fields=["status", "quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
    return criados


def distribuir_maior_resto(total, destinos):
    total_int = int(decimal_qtd(total))
    bases = []
    alocado = 0
    for destino in destinos:
        raw = Decimal(total_int) * Decimal(destino.percentual or 0) / Decimal("100")
        base = int(raw.to_integral_value(rounding=ROUND_FLOOR))
        bases.append([destino, base, raw - base])
        alocado += base
    restante = total_int - alocado
    bases.sort(key=lambda row: (-row[2], row[0].prioridade, row[0].loja_id))
    for row in bases[:restante]:
        row[1] += 1
    return {row[0].loja_id: Decimal(row[1]).quantize(Decimal("0.001")) for row in bases}


@transaction.atomic
def aplicar_perfil(distribuicao, perfil):
    if distribuicao.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
        raise ValidationError("Só é possível calcular distribuição antes da confirmação.")
    if perfil.empresa_id != distribuicao.empresa_id:
        raise ValidationError("Perfil pertence a outra empresa.")
    itens_perfil = list(perfil.itens.select_related("loja").filter(ativo=True, loja__ativo=True).exclude(loja_id=distribuicao.unidade_origem_id))
    if not itens_perfil:
        raise ValidationError("Perfil sem lojas destino ativas.")
    if perfil.tipo == PerfilDistribuicao.TIPO_PERCENTUAL:
        soma = sum((i.percentual or Decimal("0")) for i in itens_perfil)
        if abs(soma - Decimal("100.0000")) > Decimal("0.0100"):
            raise ValidationError("Perfil percentual deve totalizar 100%.")

    distribuicao.destinos.all().delete()
    for item in distribuicao.itens.all():
        if perfil.tipo == PerfilDistribuicao.TIPO_PERCENTUAL:
            alocacoes = distribuir_maior_resto(item.quantidade_selecionada, itens_perfil)
            for pitem in itens_perfil:
                qtd = alocacoes.get(pitem.loja_id, Decimal("0.000"))
                DistribuicaoDestino.objects.create(
                    distribuicao=distribuicao,
                    item=item,
                    loja_destino=pitem.loja,
                    quantidade_sugerida=qtd,
                    quantidade_ajustada=qtd,
                    percentual=pitem.percentual,
                    prioridade=pitem.prioridade,
                )
        elif perfil.tipo == PerfilDistribuicao.TIPO_FIXA:
            saldo = decimal_qtd(item.quantidade_selecionada)
            for pitem in itens_perfil:
                qtd = min(saldo, decimal_qtd(pitem.quantidade_fixa))
                saldo -= qtd
                DistribuicaoDestino.objects.create(
                    distribuicao=distribuicao,
                    item=item,
                    loja_destino=pitem.loja,
                    quantidade_sugerida=qtd,
                    quantidade_ajustada=qtd,
                    percentual=pitem.percentual,
                    prioridade=pitem.prioridade,
                )
    distribuicao.perfil = perfil
    distribuicao.tipo = perfil.tipo
    distribuicao.fator_preco = perfil.fator_preco
    distribuicao.status = Distribuicao.STATUS_CALCULADA
    distribuicao.recomputar_totais()
    distribuicao.save(update_fields=["perfil", "tipo", "fator_preco", "status", "quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])


@transaction.atomic
def montar_matriz_manual(distribuicao, lojas_destino=None):
    if distribuicao.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
        raise ValidationError("Só é possível montar matriz antes da confirmação.")
    qs = Loja.objects.filter(empresa_id=distribuicao.empresa_id, ativo=True).exclude(id=distribuicao.unidade_origem_id)
    if lojas_destino:
        qs = qs.filter(id__in=lojas_destino)
    qs = qs.exclude(tipo_unidade=Loja.TIPO_FABRICA).order_by("id")
    lojas = list(qs)
    if not lojas:
        raise ValidationError("Nenhuma loja destino disponível.")
    distribuicao.destinos.all().delete()
    for item in distribuicao.itens.all():
        for idx, loja in enumerate(lojas, start=1):
            DistribuicaoDestino.objects.create(
                distribuicao=distribuicao,
                item=item,
                loja_destino=loja,
                quantidade_sugerida=Decimal("0.000"),
                quantidade_ajustada=Decimal("0.000"),
                prioridade=idx,
            )
    distribuicao.tipo = PerfilDistribuicao.TIPO_MANUAL
    distribuicao.status = Distribuicao.STATUS_CALCULADA
    distribuicao.recomputar_totais()
    distribuicao.save(update_fields=["tipo", "status", "quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
    return len(lojas)


@transaction.atomic
def confirmar_distribuicao(distribuicao, user):
    if distribuicao.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
        raise ValidationError("Distribuição não pode ser confirmada neste status.")
    destinos = list(distribuicao.destinos.select_related("item").exclude(quantidade_ajustada=0))
    if not destinos:
        raise ValidationError("Nenhuma quantidade distribuída.")
    totais_sku = {}
    for destino in destinos:
        qtd = decimal_qtd(destino.quantidade_ajustada or destino.quantidade_sugerida)
        if qtd < 0:
            raise ValidationError("Quantidade negativa não permitida.")
        totais_sku[destino.item.ean13] = totais_sku.get(destino.item.ean13, Decimal("0.000")) + qtd

    estoques = {
        est.CodigodeBarra: est
        for est in Estoque.objects.select_for_update().filter(Idloja=distribuicao.unidade_origem, CodigodeBarra__in=totais_sku.keys())
    }
    for ean, qtd in totais_sku.items():
        est = estoques.get(ean)
        disponivel = decimal_qtd(est.Estoque if est else 0) - decimal_qtd(est.reserva if est else 0)
        if disponivel < qtd:
            raise ValidationError(f"Estoque insuficiente para o SKU {ean}.")
    for ean, qtd in totais_sku.items():
        est = estoques[ean]
        est.reserva = decimal_qtd(est.reserva) + qtd
        est.save(update_fields=["reserva"])
        EstoqueMovimentacao.objects.create(
            Idloja=distribuicao.unidade_origem,
            CodigodeBarra=ean,
            referencia=est.referencia,
            tipo=EstoqueMovimentacao.TIPO_RESERVA,
            quantidade=qtd,
            saldo_anterior=decimal_qtd(est.Estoque),
            saldo_posterior=decimal_qtd(est.Estoque),
            documento=distribuicao.numero,
            observacao="Reserva de distribuição",
        )
    for destino in destinos:
        destino.quantidade_confirmada = decimal_qtd(destino.quantidade_ajustada or destino.quantidade_sugerida)
        destino.status = DistribuicaoDestino.STATUS_CONFIRMADO
        destino.save(update_fields=["quantidade_confirmada", "status"])
    distribuicao.status = Distribuicao.STATUS_CONFIRMADA
    distribuicao.confirmado_por = user
    distribuicao.data_confirmacao = timezone.now()
    distribuicao.recomputar_totais()
    distribuicao.save(update_fields=["status", "confirmado_por", "data_confirmacao", "quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])


@transaction.atomic
def gerar_pedidos(distribuicao):
    if distribuicao.status not in {Distribuicao.STATUS_CONFIRMADA, Distribuicao.STATUS_PEDIDOS_GERADOS}:
        raise ValidationError("Confirme a distribuição antes de gerar pedidos.")
    if distribuicao.pedidos_venda.exists():
        return list(distribuicao.pedidos_venda.all())

    pedidos = []
    destinos = distribuicao.destinos.select_related("item", "loja_destino", "item__produto", "item__sku").filter(
        status=DistribuicaoDestino.STATUS_CONFIRMADO,
        quantidade_confirmada__gt=0,
    ).order_by("loja_destino_id", "item_id")
    lojas = sorted({d.loja_destino_id for d in destinos})
    for loja_id in lojas:
        loja_destinos = [d for d in destinos if d.loja_destino_id == loja_id]
        pedido = PedidoVendaDistribuicao.objects.create(
            empresa=distribuicao.empresa,
            distribuicao=distribuicao,
            numero=proximo_numero(PedidoVendaDistribuicao, distribuicao.empresa_id, "PVD"),
            unidade_origem=distribuicao.unidade_origem,
            loja_destino=loja_destinos[0].loja_destino,
            data_pedido=timezone.localdate(),
            status=PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO,
        )
        quantidade_total = Decimal("0.000")
        valor_total_custo = Decimal("0.00")
        valor_total_venda = Decimal("0.00")
        for destino in loja_destinos:
            item = destino.item
            qtd = decimal_qtd(destino.quantidade_confirmada)
            preco_unitario = Decimal(item.custo_unitario or 0) * (Decimal("1") + Decimal(distribuicao.fator_preco or 0))
            total_custo = decimal_money(qtd * item.custo_unitario)
            total_item = decimal_money(qtd * preco_unitario)
            pedido_item = PedidoVendaDistribuicaoItem.objects.create(
                pedido=pedido,
                distribuicao_destino=destino,
                produto=item.produto,
                sku=item.sku,
                referencia=item.referencia,
                descricao=item.descricao,
                cor_descricao=item.cor_descricao,
                tamanho_descricao=item.tamanho_descricao,
                ean13=item.ean13,
                quantidade=qtd,
                custo_unitario=item.custo_unitario,
                preco_unitario=preco_unitario,
                total_custo=total_custo,
                total_item=total_item,
            )
            destino.pedido = pedido
            destino.pedido_item = pedido_item
            destino.status = DistribuicaoDestino.STATUS_PEDIDO
            destino.save(update_fields=["pedido", "pedido_item", "status"])
            quantidade_total += qtd
            valor_total_custo += total_custo
            valor_total_venda += total_item
        pedido.quantidade_total = quantidade_total
        pedido.valor_total_custo = valor_total_custo
        pedido.valor_total_venda = valor_total_venda
        pedido.save(update_fields=["quantidade_total", "valor_total_custo", "valor_total_venda"])
        pedidos.append(pedido)
    distribuicao.status = Distribuicao.STATUS_PEDIDOS_GERADOS
    distribuicao.recomputar_totais()
    distribuicao.save(update_fields=["status", "quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
    return pedidos


@transaction.atomic
def recalcular_pedido(pedido):
    itens = list(pedido.itens.all())
    pedido.quantidade_total = sum((item.quantidade or Decimal("0")) for item in itens)
    pedido.valor_total_custo = sum((item.total_custo or Decimal("0")) for item in itens)
    pedido.valor_total_venda = sum((item.total_item or Decimal("0")) for item in itens)
    pedido.save(update_fields=["quantidade_total", "valor_total_custo", "valor_total_venda"])
    return pedido


@transaction.atomic
def atualizar_pedido_item(pedido, item_id, quantidade=None, preco_unitario=None):
    if pedido.status not in {PedidoVendaDistribuicao.STATUS_ABERTO, PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO}:
        raise ValidationError("Pedido não pode ser alterado neste status.")
    item = pedido.itens.select_for_update().get(pk=item_id)
    if quantidade not in (None, ""):
        qtd = decimal_qtd(quantidade)
        if qtd < 0:
            raise ValidationError("Quantidade negativa não permitida.")
        item.quantidade = qtd
        if item.distribuicao_destino_id:
            destino = item.distribuicao_destino
            destino.quantidade_confirmada = qtd
            destino.quantidade_ajustada = qtd
            destino.save(update_fields=["quantidade_confirmada", "quantidade_ajustada"])
    if preco_unitario not in (None, ""):
        preco = Decimal(str(preco_unitario or 0)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if preco < 0:
            raise ValidationError("Preço negativo não permitido.")
        item.preco_unitario = preco
    item.total_custo = decimal_money(item.quantidade * item.custo_unitario)
    item.total_item = decimal_money(item.quantidade * item.preco_unitario)
    item.save(update_fields=["quantidade", "preco_unitario", "total_custo", "total_item"])
    recalcular_pedido(pedido)
    pedido.distribuicao.recomputar_totais()
    pedido.distribuicao.save(update_fields=["quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
    return item


def _plano_por_termos(empresa, classe, termos, codigo, descricao, natureza):
    qs = PlanoContabil.objects.filter(empresa=empresa, classe=classe, ativa=True, analitica=True)
    for termo in termos:
        conta = qs.filter(descricao__icontains=termo).order_by("codigo").first()
        if conta:
            return conta
    conta, _ = PlanoContabil.objects.get_or_create(
        empresa=empresa,
        codigo=codigo,
        defaults={
            "descricao": descricao,
            "classe": classe,
            "natureza": natureza,
            "nivel": 4,
            "analitica": True,
            "ativa": True,
        },
    )
    return conta


def _natureza_faturamento_distribuicao(empresa):
    plano = _plano_por_termos(
        empresa,
        PlanoContabil.CLASSE_RECEITA,
        ("Transferência", "Faturamento", "Receita"),
        "3.1.90.001",
        "Receita de transferência para lojas",
        PlanoContabil.NATUREZA_CREDITO,
    )
    natureza = (
        Nat_Lancamento.objects
        .filter(empresa=empresa, ativo=True, natureza_operacao="RECEITA")
        .filter(Q(descricao__icontains="transfer") | Q(descricao__icontains="faturamento"))
        .order_by("codigo")
        .first()
    )
    if natureza:
        if not natureza.plano_contabil_id:
            natureza.plano_contabil = plano
            natureza.conta_contabil = plano.codigo
            natureza.save(update_fields=["plano_contabil", "conta_contabil"])
        return natureza
    return Nat_Lancamento.objects.create(
        empresa=empresa,
        codigo="REC-DIST",
        categoria_principal="Receitas",
        subcategoria="Distribuição",
        descricao="Faturamento de transferência para lojas",
        tipo="RECEITA",
        status="ATIVO",
        tipo_natureza="CREDITO",
        natureza_operacao="RECEITA",
        categoria_gerencial="Faturamento",
        movimenta_financeiro=True,
        entra_dre=True,
        plano_contabil=plano,
        conta_contabil=plano.codigo,
        ativo=True,
    )


def _natureza_cmv_distribuicao(empresa):
    plano = _plano_por_termos(
        empresa,
        PlanoContabil.CLASSE_CUSTO,
        ("CMV", "Custo", "Mercadoria"),
        "4.1.90.001",
        "CMV distribuição para lojas",
        PlanoContabil.NATUREZA_DEBITO,
    )
    natureza = (
        Nat_Lancamento.objects
        .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
        .filter(Q(descricao__icontains="CMV") | Q(descricao__icontains="custo"))
        .order_by("codigo")
        .first()
    )
    if natureza:
        if not natureza.plano_contabil_id:
            natureza.plano_contabil = plano
            natureza.conta_contabil = plano.codigo
            natureza.save(update_fields=["plano_contabil", "conta_contabil"])
        return natureza
    return Nat_Lancamento.objects.create(
        empresa=empresa,
        codigo="CMV-DIST",
        categoria_principal="Custos",
        subcategoria="Distribuição",
        descricao="CMV distribuição para lojas",
        tipo="DESPESA",
        status="ATIVO",
        tipo_natureza="DEBITO",
        natureza_operacao="DESPESA",
        categoria_gerencial="CMV",
        movimenta_financeiro=True,
        entra_dre=True,
        plano_contabil=plano,
        conta_contabil=plano.codigo,
        ativo=True,
    )


def _cliente_da_loja(loja):
    cliente = Cliente.objects.filter(empresa=loja.empresa, cpf=loja.cnpj).first()
    if cliente:
        return cliente
    return Cliente.objects.create(
        empresa=loja.empresa,
        nome_cliente=loja.nome_loja,
        apelido=loja.apelido_loja or loja.nome_loja[:18],
        cpf=loja.cnpj,
        logradouro=loja.logradouro,
        endereco=loja.endereco,
        numero=loja.numero,
        complemento=loja.complemento,
        cep=loja.cep,
        bairro=loja.bairro,
        cidade=loja.cidade,
        estado=loja.estado,
        telefone1=loja.telefone1,
        email=loja.email,
        categoria="LOJA",
        ativo=True,
    )


def _chave_nfe_simulada(nota):
    base = f"{nota.empresa_id:04d}{nota.loja_origem_id:04d}{nota.serie.zfill(3)}{nota.numero.zfill(9)}"
    return (base + "0" * 44)[:44]


def _xml_nfe_simulado(nota):
    itens_xml = []
    for idx, item in enumerate(nota.itens.all(), start=1):
        itens_xml.append(
            f"<det nItem=\"{idx}\"><prod>"
            f"<cProd>{escape(item.referencia or str(item.sku_id))}</cProd>"
            f"<cEAN>{escape(item.ean or '')}</cEAN>"
            f"<xProd>{escape(item.descricao)}</xProd>"
            f"<NCM>{escape(item.ncm or '')}</NCM>"
            f"<CFOP>{escape(item.cfop or nota.cfop or '')}</CFOP>"
            f"<qCom>{Decimal(item.quantidade or 0):.3f}</qCom>"
            f"<vUnCom>{Decimal(item.valor_unitario or 0):.4f}</vUnCom>"
            f"<vProd>{Decimal(item.valor_total or 0):.2f}</vProd>"
            f"</prod></det>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<nfeProc versao=\"4.00\" xmlns=\"http://www.portalfiscal.inf.br/nfe\">"
        f"<NFe><infNFe Id=\"NFe{nota.chave_acesso}\" versao=\"4.00\">"
        f"<ide><cUF>35</cUF><natOp>{escape(nota.natureza_operacao)}</natOp><mod>{nota.modelo}</mod>"
        f"<serie>{escape(nota.serie)}</serie><nNF>{escape(nota.numero)}</nNF><dhEmi>{timezone.now().isoformat()}</dhEmi></ide>"
        f"<emit><xNome>{escape(nota.loja_origem.nome_loja)}</xNome><CNPJ>{escape(nota.loja_origem.cnpj or '')}</CNPJ></emit>"
        f"<dest><xNome>{escape(nota.loja_destino.nome_loja if nota.loja_destino else '')}</xNome><CNPJ>{escape(nota.loja_destino.cnpj if nota.loja_destino else '')}</CNPJ></dest>"
        f"{''.join(itens_xml)}"
        f"<total><ICMSTot><vProd>{Decimal(nota.valor_produtos or 0):.2f}</vProd><vNF>{Decimal(nota.valor_total or 0):.2f}</vNF></ICMSTot></total>"
        "</infNFe></NFe>"
        f"<protNFe><infProt><chNFe>{nota.chave_acesso}</chNFe><nProt>{escape(nota.protocolo_autorizacao)}</nProt><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>"
        "</nfeProc>"
    )


def _agregar_itens_pedidos(pedidos):
    itens = {}
    for pedido in pedidos:
        for item in pedido.itens.select_related("produto", "sku", "distribuicao_destino"):
            key = (item.sku_id, str(item.preco_unitario), str(item.custo_unitario))
            if key not in itens:
                itens[key] = {
                    "produto": item.produto,
                    "sku": item.sku,
                    "ean13": item.ean13,
                    "referencia": item.referencia,
                    "descricao": item.descricao,
                    "cor_descricao": item.cor_descricao,
                    "tamanho_descricao": item.tamanho_descricao,
                    "quantidade": Decimal("0.000"),
                    "preco_unitario": item.preco_unitario,
                    "custo_unitario": item.custo_unitario,
                    "pedido_itens": [],
                }
            itens[key]["quantidade"] += decimal_qtd(item.quantidade)
            itens[key]["pedido_itens"].append(item)
    return list(itens.values())


@transaction.atomic
def gerar_notas_faturamento_distribuicao(pedidos, user=None):
    pedidos = list(pedidos)
    if not pedidos:
        raise ValidationError("Selecione ao menos um pedido para gerar nota.")
    for pedido in pedidos:
        if pedido.status not in {PedidoVendaDistribuicao.STATUS_ABERTO, PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO}:
            raise ValidationError(f"Pedido {pedido.numero} não pode gerar nota neste status.")
        if pedido.nfe_numero:
            raise ValidationError(f"Pedido {pedido.numero} já possui NF-e gerada.")

    notas = []
    lojas = sorted({pedido.loja_destino_id for pedido in pedidos})
    for loja_id in lojas:
        grupo = [pedido for pedido in pedidos if pedido.loja_destino_id == loja_id]
        origens = {pedido.unidade_origem_id for pedido in grupo}
        if len(origens) != 1:
            raise ValidationError("Pedidos do mesmo destinatário devem ter a mesma origem para compor uma NF-e.")
        origem = grupo[0].unidade_origem
        destino = grupo[0].loja_destino
        serie, numero = proxima_nfe(origem)
        hoje = timezone.localdate()
        documentos = ", ".join(p.numero for p in grupo)
        nota = NotaFiscalSaida.objects.create(
            empresa=grupo[0].empresa,
            loja_origem=origem,
            loja_destino=destino,
            tipo_operacao=NotaFiscalSaida.TipoOperacao.TRANSFERENCIA,
            modelo="55",
            serie=serie,
            numero=numero,
            documento_origem=documentos[:50],
            cfop="5152",
            natureza_operacao="Transferência de mercadoria para loja",
            status=NotaFiscalSaida.Status.PRONTA,
            dt_emissao=hoje,
            dt_saida=hoje,
            observacoes=f"NF-e agrupada dos pedidos: {documentos}"[:255],
            criado_por=user if getattr(user, "is_authenticated", False) else None,
        )
        for item in _agregar_itens_pedidos(grupo):
            NotaFiscalSaidaItem.objects.create(
                nota=nota,
                produto=item["produto"],
                sku=item["sku"],
                ean=item["ean13"],
                referencia=item["referencia"],
                descricao=item["descricao"],
                cor=item["cor_descricao"] or "",
                tamanho=item["tamanho_descricao"] or "",
                ncm=item["produto"].ncm or "",
                cfop="5152",
                quantidade=item["quantidade"],
                valor_unitario=item["preco_unitario"],
            )
        nota.recalcular_totais()
        for pedido in grupo:
            pedido.faturamento_status = "NOTA_GERADA"
            pedido.nfe_numero = nota.numero
            pedido.nfe_status = nota.status
            pedido.nfe_data = timezone.now()
            pedido.save(update_fields=["faturamento_status", "nfe_numero", "nfe_status", "nfe_data"])
        notas.append(nota)
    return notas


@transaction.atomic
def autorizar_nota_distribuicao(nota, user=None):
    if nota.status == NotaFiscalSaida.Status.AUTORIZADA:
        return nota
    if nota.status not in {NotaFiscalSaida.Status.DIGITADA, NotaFiscalSaida.Status.PRONTA}:
        raise ValidationError("NF-e não pode ser autorizada neste status.")
    pedidos = list(
        PedidoVendaDistribuicao.objects
        .select_related("empresa", "distribuicao", "unidade_origem", "loja_destino")
        .prefetch_related("itens")
        .filter(
            empresa=nota.empresa,
            unidade_origem=nota.loja_origem,
            loja_destino=nota.loja_destino,
            nfe_numero=nota.numero,
            status__in=[PedidoVendaDistribuicao.STATUS_ABERTO, PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO],
        )
    )
    if not pedidos:
        raise ValidationError("Nenhum pedido de distribuição vinculado a esta NF-e.")

    itens_pedido = [item for pedido in pedidos for item in pedido.itens.select_related("produto", "sku", "distribuicao_destino")]
    eans = sorted({item.ean13 for item in itens_pedido})
    estoques = {
        est.CodigodeBarra: est
        for est in Estoque.objects.select_for_update().filter(Idloja=nota.loja_origem, CodigodeBarra__in=eans)
    }
    totais_ean = {}
    for item in itens_pedido:
        qtd = decimal_qtd(item.quantidade)
        totais_ean[item.ean13] = totais_ean.get(item.ean13, Decimal("0.000")) + qtd
    for ean, qtd in totais_ean.items():
        est = estoques.get(ean)
        if not est:
            raise ValidationError(f"Estoque não encontrado para SKU {ean}.")
        if decimal_qtd(est.Estoque) < qtd:
            raise ValidationError(f"Estoque insuficiente para SKU {ean}.")
        if decimal_qtd(est.reserva) < qtd:
            raise ValidationError(f"Reserva insuficiente para SKU {ean}.")

    nota.chave_acesso = nota.chave_acesso or _chave_nfe_simulada(nota)
    nota.protocolo_autorizacao = nota.protocolo_autorizacao or f"{timezone.now():%Y%m%d%H%M%S}{nota.pk}"
    nota.status = NotaFiscalSaida.Status.AUTORIZADA
    nota.autorizada_em = timezone.now()
    nota.xml = _xml_nfe_simulado(nota)
    nota.save(update_fields=["chave_acesso", "protocolo_autorizacao", "status", "autorizada_em", "xml", "atualizado_em"])

    natureza_receita = _natureza_faturamento_distribuicao(nota.empresa)
    natureza_cmv = _natureza_cmv_distribuicao(nota.empresa)
    cliente = _cliente_da_loja(nota.loja_destino)

    receber = Receber.objects.filter(empresa=nota.empresa, nfe_id=nota.pk).first()
    if not receber:
        receber = Receber.objects.create(
            empresa=nota.empresa,
            idloja=nota.loja_origem,
            idcliente=cliente,
            Titulo=f"NFE {nota.serie}/{nota.numero}",
            Documento=nota.numero,
            Data_emissao=nota.dt_emissao,
            Valor_total=nota.valor_total,
            Previsao=False,
            FormaPagamento="A PRAZO",
            Idnatureza=natureza_receita,
            conta_contabil=natureza_receita.conta_contabil,
            nfe_id=nota.pk,
        )
        parcela = ReceberItem.objects.create(
            Idreceber=receber,
            parcela_n=1,
            status=ReceberItem.STATUS_EFETIVO,
            Data_vencimento=nota.dt_emissao,
            valor_parcela=nota.valor_total,
            FormaPagamento="A PRAZO",
            Previsao=False,
            Idnatureza=natureza_receita,
        )
        ReceberRateio.objects.create(Idreceberitem=parcela, Idnatureza=natureza_receita, valor=nota.valor_total)
        mov_receita = MovimentacaoFinanceira.objects.create(
            empresa=nota.empresa,
            idloja=nota.loja_origem,
            data_movimento=nota.dt_emissao,
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_RECEBER,
            valor=nota.valor_total,
            historico=f"Receita NF-e distribuição {nota.numero}",
            documento=nota.numero,
            Idnatureza=natureza_receita,
            receber_item=parcela,
        )
        gerar_lancamento_contabil_movimentacao(mov_receita)

    total_cmv = Decimal("0.00")
    for item in itens_pedido:
        est = estoques[item.ean13]
        qtd = decimal_qtd(item.quantidade)
        anterior = decimal_qtd(est.Estoque)
        est.Estoque = anterior - qtd
        est.reserva = max(Decimal("0.000"), decimal_qtd(est.reserva) - qtd)
        est.save(update_fields=["Estoque", "reserva"])
        total_cmv += decimal_money(qtd * item.custo_unitario)
        EstoqueMovimentacao.objects.create(
            Idloja=nota.loja_origem,
            CodigodeBarra=item.ean13,
            referencia=item.referencia,
            tipo=EstoqueMovimentacao.TIPO_SAIDA,
            quantidade=qtd,
            custo_unitario=item.custo_unitario,
            custo_total=decimal_money(qtd * item.custo_unitario),
            saldo_anterior=anterior,
            saldo_posterior=est.Estoque,
            documento=nota.numero,
            observacao="Autorização NF-e distribuição",
        )
        if item.distribuicao_destino_id:
            MercadoriaTransito.objects.get_or_create(
                pedido=item.pedido,
                pedido_item=item,
                distribuicao_destino=item.distribuicao_destino,
                defaults={
                    "unidade_origem": nota.loja_origem,
                    "loja_destino": nota.loja_destino,
                    "sku": item.sku,
                    "ean13": item.ean13,
                    "quantidade_enviada": qtd,
                    "data_envio": timezone.now(),
                    "status": MercadoriaTransito.STATUS_EM_TRANSITO,
                },
            )
    if total_cmv:
        mov_cmv = MovimentacaoFinanceira.objects.create(
            empresa=nota.empresa,
            idloja=nota.loja_origem,
            data_movimento=nota.dt_emissao,
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total_cmv,
            historico=f"CMV NF-e distribuição {nota.numero}",
            documento=nota.numero,
            Idnatureza=natureza_cmv,
        )
        gerar_lancamento_contabil_movimentacao(mov_cmv)

    for pedido in pedidos:
        pedido.status = PedidoVendaDistribuicao.STATUS_FATURADO
        pedido.faturamento_status = "FATURADO"
        pedido.nfe_numero = nota.numero
        pedido.nfe_chave = nota.chave_acesso
        pedido.nfe_status = nota.status
        pedido.nfe_data = timezone.now()
        pedido.save(update_fields=["status", "faturamento_status", "nfe_numero", "nfe_chave", "nfe_status", "nfe_data"])
        distribuicao = pedido.distribuicao
        if not distribuicao.pedidos_venda.exclude(status=PedidoVendaDistribuicao.STATUS_FATURADO).exists():
            distribuicao.status = Distribuicao.STATUS_EM_TRANSITO
        else:
            distribuicao.status = Distribuicao.STATUS_EM_FATURAMENTO
        distribuicao.save(update_fields=["status", "atualizado_em"])
    return nota


@transaction.atomic
def faturar_pedido_distribuicao(pedido, user=None):
    if pedido.status == PedidoVendaDistribuicao.STATUS_FATURADO:
        return pedido
    if pedido.nfe_numero:
        nota = NotaFiscalSaida.objects.filter(empresa=pedido.empresa, numero=pedido.nfe_numero, loja_destino=pedido.loja_destino).first()
    else:
        notas = gerar_notas_faturamento_distribuicao([pedido], user)
        nota = notas[0]
    if not nota:
        raise ValidationError("NF-e vinculada ao pedido não encontrada.")
    autorizar_nota_distribuicao(nota, user)
    pedido.refresh_from_db()
    return pedido


@transaction.atomic
def cancelar_distribuicao(distribuicao, motivo=""):
    if distribuicao.status in {Distribuicao.STATUS_FATURADA, Distribuicao.STATUS_EM_TRANSITO, Distribuicao.STATUS_RECEBIDA_PARCIAL, Distribuicao.STATUS_RECEBIDA}:
        raise ValidationError("Distribuição com faturamento, trânsito ou recebimento não pode ser cancelada por este módulo.")
    if distribuicao.status in {Distribuicao.STATUS_CONFIRMADA, Distribuicao.STATUS_PEDIDOS_GERADOS}:
        totais_sku = {}
        for destino in distribuicao.destinos.filter(quantidade_confirmada__gt=0):
            totais_sku[destino.item.ean13] = totais_sku.get(destino.item.ean13, Decimal("0.000")) + decimal_qtd(destino.quantidade_confirmada)
        estoques = {
            est.CodigodeBarra: est
            for est in Estoque.objects.select_for_update().filter(Idloja=distribuicao.unidade_origem, CodigodeBarra__in=totais_sku.keys())
        }
        for ean, qtd in totais_sku.items():
            est = estoques.get(ean)
            if est:
                est.reserva = max(Decimal("0.000"), decimal_qtd(est.reserva) - qtd)
                est.save(update_fields=["reserva"])
        distribuicao.pedidos_venda.filter(status__in=[PedidoVendaDistribuicao.STATUS_ABERTO, PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO]).update(status=PedidoVendaDistribuicao.STATUS_CANCELADO, faturamento_status="CANCELADO")
        distribuicao.destinos.update(status=DistribuicaoDestino.STATUS_CANCELADO)
    distribuicao.status = Distribuicao.STATUS_CANCELADA
    distribuicao.motivo_cancelamento = motivo or ""
    distribuicao.data_cancelamento = timezone.now()
    distribuicao.save(update_fields=["status", "motivo_cancelamento", "data_cancelamento", "atualizado_em"])
    return distribuicao


def _atualizar_status_recebimento_distribuicao(distribuicao):
    transitos = MercadoriaTransito.objects.filter(pedido__distribuicao=distribuicao)
    if not transitos.exists():
        return
    if not transitos.exclude(status=MercadoriaTransito.STATUS_RECEBIDA).exists():
        distribuicao.status = Distribuicao.STATUS_RECEBIDA
    elif transitos.filter(status__in=[MercadoriaTransito.STATUS_RECEBIDA, MercadoriaTransito.STATUS_DIVERGENTE]).exists():
        distribuicao.status = Distribuicao.STATUS_RECEBIDA_PARCIAL
    else:
        return
    distribuicao.save(update_fields=["status", "atualizado_em"])


@transaction.atomic
def confirmar_recebimento(transito, quantidade_recebida, documento=None):
    if transito.status != MercadoriaTransito.STATUS_EM_TRANSITO:
        raise ValidationError("Recebimento permitido apenas para mercadoria em trânsito.")
    qtd = decimal_qtd(quantidade_recebida)
    if qtd < 0 or qtd > decimal_qtd(transito.quantidade_enviada):
        raise ValidationError("Quantidade recebida inválida.")
    estoque, _ = Estoque.objects.select_for_update().get_or_create(
        Idloja=transito.loja_destino,
        CodigodeBarra=transito.ean13,
        defaults={"referencia": transito.pedido_item.referencia, "Estoque": 0, "reserva": 0},
    )
    anterior = decimal_qtd(estoque.Estoque)
    estoque.Estoque = anterior + qtd
    estoque.save(update_fields=["Estoque"])
    transito.quantidade_recebida = qtd
    transito.quantidade_divergente = decimal_qtd(transito.quantidade_enviada) - qtd
    transito.data_recebimento = timezone.now()
    transito.status = MercadoriaTransito.STATUS_RECEBIDA if transito.quantidade_divergente == 0 else MercadoriaTransito.STATUS_DIVERGENTE
    transito.save(update_fields=["quantidade_recebida", "quantidade_divergente", "data_recebimento", "status"])
    EstoqueMovimentacao.objects.create(
        Idloja=transito.loja_destino,
        CodigodeBarra=transito.ean13,
        referencia=transito.pedido_item.referencia,
        tipo=EstoqueMovimentacao.TIPO_ENTRADA,
        quantidade=qtd,
        custo_unitario=transito.pedido_item.custo_unitario,
        custo_total=decimal_money(qtd * transito.pedido_item.custo_unitario),
        saldo_anterior=anterior,
        saldo_posterior=estoque.Estoque,
        documento=documento or transito.pedido.nfe_numero or transito.pedido.numero,
        observacao="Recebimento de distribuição",
    )
    _atualizar_status_recebimento_distribuicao(transito.pedido.distribuicao)
    return transito


@transaction.atomic
def confirmar_recebimento_nota(transitos, itens):
    transitos = list(transitos)
    if not transitos:
        raise ValidationError("Nenhuma mercadoria em trânsito encontrada para recebimento.")
    recebidos = {int(item.get("transito")): item.get("quantidade_recebida") for item in itens or [] if item.get("transito")}
    if not recebidos:
        raise ValidationError("Informe as quantidades conferidas.")
    ids_transitos = {t.pk for t in transitos}
    ids_recebidos = set(recebidos.keys())
    if ids_recebidos - ids_transitos:
        raise ValidationError("Item de recebimento inválido para esta NF-e.")
    atualizados = []
    for transito in transitos:
        if transito.pk not in recebidos:
            continue
        atualizados.append(confirmar_recebimento(transito, recebidos[transito.pk], documento=transito.pedido.nfe_numero))
    return atualizados

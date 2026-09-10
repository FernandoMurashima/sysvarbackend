from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from distribuicao.models import Distribuicao, DistribuicaoItem
from fiscal.models import NotaFiscalEntradaItem, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaEstoque
from produto.models import EstoqueMovimentacao, ProdutoDetalhe


ZERO = Decimal("0")


def q4(value):
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def q2(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def custo_unitario_linha_conferencia(linha):
    item_nf = item_nf_linha_conferencia(linha)
    if item_nf and Decimal(item_nf.preco_unit_nf or 0) > 0:
        return q4(item_nf.preco_unit_nf)
    return q4(linha.pedido_item.preco_unit if linha.pedido_item_id else 0)


def item_nf_linha_conferencia(linha):
    qs = NotaFiscalEntradaItem.objects.filter(
        pedido_item=linha.pedido_item,
        nota__empresa_id=linha.recebimento.empresa_id,
    )
    if linha.recebimento.xml_fornecedor_id:
        qs = qs.filter(nota__xml_fornecedor_id=linha.recebimento.xml_fornecedor_id)
    item_nf = qs.order_by("-nota__dt_entrada", "-nota_id", "-id").first()
    if item_nf:
        return item_nf
    return (
        NotaFiscalEntradaItem.objects
        .filter(pedido_item=linha.pedido_item, nota__empresa_id=linha.recebimento.empresa_id)
        .order_by("-nota__dt_entrada", "-nota_id", "-id")
        .first()
    )


def custos_recebimento_por_ean(recebimento):
    acumulado = {}
    linhas = (
        RecebimentoMercadoriaConferenciaItem.objects
        .select_related("recebimento", "pedido_item", "produto_detalhe", "produto_detalhe__produto")
        .filter(recebimento=recebimento, produto_detalhe__isnull=False, quantidade_recebida__gt=0)
    )
    for linha in linhas:
        ean = linha.produto_detalhe.ean13
        qtd = Decimal(linha.quantidade_recebida or 0)
        custo = custo_unitario_linha_conferencia(linha)
        if not ean or qtd <= 0 or custo <= 0:
            continue
        bucket = acumulado.setdefault(ean, {"quantidade": ZERO, "valor": ZERO})
        bucket["quantidade"] += qtd
        bucket["valor"] += qtd * custo
    return {
        ean: q4(dados["valor"] / dados["quantidade"])
        for ean, dados in acumulado.items()
        if dados["quantidade"] > 0
    }


def atualizar_custo_medio_sku(sku, saldo_anterior, quantidade, custo_entrada):
    custo_entrada = q4(custo_entrada)
    custo_atual = q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
    if custo_entrada <= 0:
        return custo_atual

    saldo_anterior_dec = Decimal(max(int(Decimal(saldo_anterior or 0)), 0))
    quantidade_dec = Decimal(max(int(Decimal(quantidade or 0)), 0))
    saldo_posterior = saldo_anterior_dec + quantidade_dec
    if saldo_posterior <= 0 or saldo_anterior_dec <= 0 or custo_atual <= 0:
        custo_medio = custo_entrada
    else:
        custo_medio = ((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo_entrada)) / saldo_posterior

    sku.custo_ultima_compra = custo_entrada
    if not Decimal(sku.custo_original or 0):
        sku.custo_original = custo_entrada
    sku.custo_medio = q4(custo_medio)
    sku.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
    sincronizar_custos_produto_pai(sku.produto)
    return sku.custo_medio


def sincronizar_custos_produto_pai(produto):
    custos = [
        Decimal(custo or 0)
        for custo in ProdutoDetalhe.objects.filter(produto=produto, custo_medio__gt=0).values_list("custo_medio", flat=True)
        if Decimal(custo or 0) > 0
    ]
    if not custos:
        return
    custo_medio = q4(sum(custos, ZERO) / Decimal(len(custos)))
    produto.custo_ultima_compra = custo_medio
    produto.custo_medio = custo_medio
    if not Decimal(produto.custo_original or 0):
        produto.custo_original = custo_medio
    produto.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])


def custo_distribuicao_sku(sku):
    produto = sku.produto
    return q4(
        sku.custo_medio
        or sku.custo_ultima_compra
        or sku.custo_original
        or produto.custo_medio
        or produto.custo_ultima_compra
        or produto.custo_original
        or 0
    )


def atualizar_distribuicoes_editaveis(empresa_id=None):
    qs = Distribuicao.objects.filter(status__in=[Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA])
    if empresa_id:
        qs = qs.filter(empresa_id=empresa_id)
    atualizadas = 0
    itens_atualizados = 0
    for distribuicao in qs.select_for_update().order_by("empresa_id", "id"):
        alterou = False
        for item in distribuicao.itens.select_related("sku", "sku__produto").all():
            custo = custo_distribuicao_sku(item.sku)
            custo_total = q2(Decimal(item.quantidade_selecionada or 0) * custo)
            if q4(item.custo_unitario) == custo and q2(item.custo_total) == custo_total:
                continue
            item.custo_unitario = custo
            item.custo_total = custo_total
            item.save(update_fields=["custo_unitario", "custo_total"])
            itens_atualizados += 1
            alterou = True
        if alterou:
            distribuicao.recomputar_totais()
            distribuicao.save(update_fields=["quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
            atualizadas += 1
    bloqueadas_com_custo_zero = DistribuicaoItem.objects.filter(
        distribuicao__status__in=[
            Distribuicao.STATUS_CONFIRMADA,
            Distribuicao.STATUS_PEDIDOS_GERADOS,
            Distribuicao.STATUS_EM_FATURAMENTO,
            Distribuicao.STATUS_FATURADA,
            Distribuicao.STATUS_EM_TRANSITO,
            Distribuicao.STATUS_RECEBIDA_PARCIAL,
            Distribuicao.STATUS_RECEBIDA,
        ],
        custo_unitario=0,
    )
    if empresa_id:
        bloqueadas_com_custo_zero = bloqueadas_com_custo_zero.filter(distribuicao__empresa_id=empresa_id)
    return {
        "distribuicoes_atualizadas": atualizadas,
        "distribuicao_itens_atualizados": itens_atualizados,
        "distribuicoes_bloqueadas_com_custo_zero": bloqueadas_com_custo_zero.values("distribuicao_id").distinct().count(),
    }


@transaction.atomic
def reparar_custos_recebimentos_mercadoria(empresa_id=None):
    recebimentos = (
        RecebimentoMercadoriaEstoque.objects
        .select_related("empresa", "loja", "efetivacao_estoque")
        .filter(status=RecebimentoMercadoriaEstoque.Status.CONCLUIDO, efetivacao_estoque__isnull=False)
        .exclude(status=RecebimentoMercadoriaEstoque.Status.CANCELADO)
        .order_by("efetivacao_estoque__efetivado_em", "id")
    )
    if empresa_id:
        recebimentos = recebimentos.filter(empresa_id=empresa_id)

    stats = {
        "recebimentos_analisados": 0,
        "skus_corrigidos": 0,
        "movimentacoes_corrigidas": 0,
    }
    skus_tocados = set()
    custos_correntes = {}
    for recebimento in recebimentos:
        stats["recebimentos_analisados"] += 1
        custos = custos_recebimento_por_ean(recebimento)
        for mov in EstoqueMovimentacao.objects.select_for_update().filter(
            origem=EstoqueMovimentacao.ORIGEM_RECEBIMENTO_MERCADORIA,
            documento=f"RECEB-{recebimento.id}",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade__gt=0,
        ).order_by("data_movimento", "Idmovimento"):
            custo = custos.get(mov.CodigodeBarra)
            if not custo or custo <= 0:
                continue
            sku = ProdutoDetalhe.objects.select_for_update().select_related("produto").filter(
                ean13=mov.CodigodeBarra,
                produto__empresa_id=recebimento.empresa_id,
            ).first()
            if not sku:
                continue
            custo_atual = custos_correntes.setdefault(sku.pk, q4(sku.custo_original or sku.custo_medio or sku.custo_ultima_compra or 0))
            saldo_anterior_dec = Decimal(max(int(Decimal(mov.saldo_anterior or 0)), 0))
            quantidade_dec = Decimal(max(int(Decimal(mov.quantidade or 0)), 0))
            if saldo_anterior_dec <= 0 or custo_atual <= 0:
                custo_medio_apos = custo
            else:
                custo_medio_apos = q4(((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo)) / (saldo_anterior_dec + quantidade_dec))
            custos_correntes[sku.pk] = custo_medio_apos

            sku_alterado = q4(sku.custo_ultima_compra) != custo or q4(sku.custo_medio) != custo_medio_apos or (not Decimal(sku.custo_original or 0) and custo > 0)
            sku.custo_ultima_compra = custo
            if not Decimal(sku.custo_original or 0):
                sku.custo_original = custo
            sku.custo_medio = custo_medio_apos
            sku.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
            sincronizar_custos_produto_pai(sku.produto)
            if sku_alterado and sku.pk not in skus_tocados:
                skus_tocados.add(sku.pk)
                stats["skus_corrigidos"] += 1
            custo_total = q2(Decimal(mov.quantidade or 0) * custo)
            if q4(mov.custo_unitario) != custo or q2(mov.custo_total) != custo_total or q4(mov.custo_medio_apos) != custo_medio_apos:
                mov.custo_unitario = custo
                mov.custo_total = custo_total
                mov.custo_medio_apos = custo_medio_apos
                mov.save(update_fields=["custo_unitario", "custo_total", "custo_medio_apos"])
                stats["movimentacoes_corrigidas"] += 1

    stats.update(atualizar_distribuicoes_editaveis(empresa_id=empresa_id))
    stats["recebimentos_com_custo"] = len(skus_tocados)
    return stats

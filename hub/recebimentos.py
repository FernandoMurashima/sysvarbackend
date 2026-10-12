import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from compras.services_recebimento import saldos_pedido_por_sku, sincronizar_atendimento_pedido_compra
from fiscal.models import (
    RecebimentoMercadoriaEfetivacaoEstoque,
    RecebimentoMercadoriaEstoque,
    RecebimentoMercadoriaTermo,
    XmlFornecedorRecebido,
)
from fiscal.services.recebimento_custos import atualizar_custo_medio_sku, custos_recebimento_por_ean, q2, q4
from produto.models import Estoque, EstoqueMovimentacao, ProdutoDetalhe


class RecebimentoHubError(Exception):
    pass


def recebimentos_hub_queryset(hub):
    loja = hub.loja
    return (
        RecebimentoMercadoriaEstoque.objects
        .select_related("empresa", "loja", "fornecedor", "xml_fornecedor", "termo_encerramento", "efetivacao_estoque")
        .prefetch_related(
            "pedidos_vinculados__pedido",
            "pedidos_vinculados__pedido__itens",
            "conferencia_itens__produto",
            "conferencia_itens__cor",
            "conferencia_itens__tamanho",
            "conferencia_itens__produto_detalhe",
        )
        .filter(empresa=loja.empresa)
        .filter(Q(loja=loja) | Q(loja__isnull=True, pedidos_vinculados__pedido__loja=loja))
        .exclude(status=RecebimentoMercadoriaEstoque.Status.CANCELADO)
        .distinct()
        .order_by("-criado_em", "-id")
    )


def recebimento_pertence_ao_hub(recebimento, hub):
    if recebimento.empresa_id != hub.loja.empresa_id:
        return False
    loja = loja_destino_estoque(recebimento)
    return bool(loja and loja.pk == hub.loja_id)


def serializar_recebimento_hub(recebimento, detalhado=False):
    xml = recebimento.xml_fornecedor
    conferencia_itens = list(
        recebimento.conferencia_itens.select_related("pedido", "pedido_item", "produto", "cor", "tamanho", "produto_detalhe").all()
    )
    esperado = sum((item.quantidade_esperada or 0) for item in conferencia_itens)
    recebido = sum((item.quantidade_recebida or 0) for item in conferencia_itens)
    payload = {
        "id": recebimento.pk,
        "recebimento_uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"central-recebimento:{recebimento.pk}")),
        "status": recebimento.status,
        "origem": "NFE_FORNECEDOR" if recebimento.xml_fornecedor_id else "PEDIDO_COMPRA",
        "criado_em": recebimento.criado_em,
        "atualizado_em": recebimento.atualizado_em,
        "loja": _loja_payload(recebimento.loja),
        "fornecedor": _fornecedor_payload(recebimento.fornecedor),
        "documento_fiscal": _documento_fiscal_payload(xml),
        "pedidos": [
            {
                "id": vinculo.pedido_id,
                "numero": str(vinculo.pedido_id),
                "emissao": vinculo.pedido.emissao,
                "status": vinculo.pedido.status,
                "valor_total": str(vinculo.pedido.total_pedido or 0),
            }
            for vinculo in recebimento.pedidos_vinculados.all()
        ],
        "resumo": {
            "quantidade_esperada_total": str(esperado),
            "quantidade_recebida_total": str(recebido),
            "diferenca_total": str(recebido - esperado),
            "quantidade_skus": len(conferencia_itens),
            "quantidade_skus_com_divergencia": sum(1 for item in conferencia_itens if item.diferenca != 0),
            "estoque_efetivado": hasattr(recebimento, "efetivacao_estoque"),
        },
        "contrato_retorno": {
            "tipo_evento": "RECEBIMENTO_LOJA_CONFERIDO",
            "identificador_operacao": "evento_uuid + chave_idempotencia",
            "exige_operador_retaguarda": True,
        },
    }
    if detalhado:
        payload["itens"] = [_serializar_item_conferencia(item) for item in conferencia_itens]
        payload["termo_encerramento"] = _termo_payload(getattr(recebimento, "termo_encerramento", None))
        payload["efetivacao_estoque"] = _efetivacao_payload(getattr(recebimento, "efetivacao_estoque", None))
    return payload


@transaction.atomic
def processar_retorno_conferencia_hub(hub, payload):
    recebimento_id = payload.get("recebimento_id") or payload.get("id")
    if not recebimento_id:
        raise RecebimentoHubError("Informe recebimento_id.")
    recebimento = (
        RecebimentoMercadoriaEstoque.objects
        .select_for_update()
        .select_related("empresa", "loja", "fornecedor", "xml_fornecedor")
        .filter(pk=recebimento_id)
        .first()
    )
    if not recebimento or not recebimento_pertence_ao_hub(recebimento, hub):
        raise RecebimentoHubError("Recebimento não encontrado para a loja deste Hub.")

    efetivacao = getattr(recebimento, "efetivacao_estoque", None)
    if efetivacao:
        return _resultado_recebimento(recebimento, efetivacao=efetivacao)

    termo = getattr(recebimento, "termo_encerramento", None)
    if termo is None:
        if recebimento.status != RecebimentoMercadoriaEstoque.Status.EM_CONFERENCIA:
            raise RecebimentoHubError("Recebimento deve estar em conferência para receber retorno do Hub.")
        operador = _operador_retaguarda(hub, payload)
        _aplicar_quantidades_conferencia(recebimento, payload.get("itens") or [])
        recebimento.refresh_from_db()
        observacao = str(payload.get("observacao_divergencia") or payload.get("observacao") or "").strip()
        encerrado_em = timezone.now()
        snapshot, possui_divergencia = snapshot_termo_recebimento(recebimento, operador, observacao, encerrado_em)
        if possui_divergencia and not observacao:
            raise RecebimentoHubError("Informe observacao_divergencia para encerramento com divergência.")
        termo = RecebimentoMercadoriaTermo.objects.create(
            recebimento=recebimento,
            empresa=recebimento.empresa,
            encerrado_por=operador,
            encerrado_em=encerrado_em,
            observacao_divergencia=observacao,
            possui_divergencia=possui_divergencia,
            snapshot=snapshot,
            hash_sha256=_hash_snapshot(snapshot),
        )
        recebimento.status = RecebimentoMercadoriaEstoque.Status.CONCLUIDO
        recebimento.save(update_fields=["status", "xml_fornecedor_ativo_key", "atualizado_em"])
    else:
        operador = termo.encerrado_por

    efetivacao = efetivar_estoque_recebimento(recebimento, termo, operador)
    return _resultado_recebimento(recebimento, termo=termo, efetivacao=efetivacao)


def loja_destino_estoque(recebimento):
    if recebimento.loja_id:
        return recebimento.loja
    pedidos = [v.pedido for v in recebimento.pedidos_vinculados.select_related("pedido__loja").all() if v.pedido.loja_id]
    lojas = {pedido.loja_id: pedido.loja for pedido in pedidos}
    return next(iter(lojas.values())) if len(lojas) == 1 else None


def snapshot_termo_recebimento(recebimento, user, observacao, encerrado_em):
    xml = recebimento.xml_fornecedor
    pedidos_vinculados = list(recebimento.pedidos_vinculados.select_related("pedido", "pedido__fornecedor", "pedido__loja").prefetch_related("pedido__itens"))
    itens = list(recebimento.conferencia_itens.select_related("pedido", "pedido_item", "produto", "cor", "tamanho", "produto_detalhe").order_by("produto__referencia", "cor__Descricao", "tamanho__Tamanho", "id"))
    pedido_itens = [item for vinculo in pedidos_vinculados for item in vinculo.pedido.itens.all()]
    saldos = saldos_pedido_por_sku(pedido_itens, recebimento.empresa_id, excluir_recebimento_id=recebimento.id)
    pedido_original_total = sum((item.qtd or 0) for item in pedido_itens)
    pedido_pendente_total = sum(saldo["quantidade_pendente"] for saldo in saldos.values())
    fisico_total = sum((item.quantidade_recebida or 0) for item in itens)
    nfe_total = xml.quantidade_total_faturada if xml else None
    estruturado = _quantidades_nfe_por_ean(recebimento) is not None
    linhas = []
    faltas = []
    sobras = []
    referencias_recebidas = set()
    skus_recebidos = set()
    for item in itens:
        diferenca = item.diferenca
        situacao = "OK" if diferenca == 0 else "SOBRA" if diferenca > 0 else "FALTA"
        linha = {
            "pedido_id": item.pedido_id,
            "pedido_item_id": item.pedido_item_id,
            "referencia": item.produto.referencia or "",
            "produto": item.produto.descricao or "",
            "cor": item.cor.Descricao or "",
            "tamanho": item.tamanho.Tamanho or "",
            "ean": item.produto_detalhe.ean13 or "",
            "esperado": str(item.quantidade_esperada),
            "recebido": str(item.quantidade_recebida),
            "diferenca": str(diferenca),
            "situacao": situacao,
            "quantidade_pedido_pendente": str((saldos.get((item.pedido_item_id, item.produto_detalhe_id), {}) or {}).get("quantidade_pendente", Decimal("0"))),
        }
        linhas.append(linha)
        if item.quantidade_recebida and item.quantidade_recebida > 0:
            referencias_recebidas.add(item.produto.referencia or str(item.produto_id))
            skus_recebidos.add(item.produto_detalhe_id)
        if diferenca < 0:
            faltas.append(linha)
        elif diferenca > 0:
            sobras.append(linha)
    possui_divergencia = (
        ((nfe_total is not None and fisico_total != nfe_total) or any(item.diferenca != 0 for item in itens))
        if estruturado
        else (fisico_total != pedido_original_total or (nfe_total is not None and fisico_total != nfe_total) or any(item.diferenca != 0 for item in itens))
    )
    snapshot = {
        "recebimento": {
            "id": recebimento.id,
            "status_anterior": recebimento.status,
            "criado_em": recebimento.criado_em.isoformat() if recebimento.criado_em else None,
            "encerrado_em": encerrado_em.isoformat(),
            "origem": "HUB",
        },
        "xml_nfe": _documento_fiscal_payload(xml),
        "estabelecimento": _loja_payload(loja_destino_estoque(recebimento)),
        "pedidos_vinculados": [
            {
                "id": vinculo.pedido.id,
                "numero": str(vinculo.pedido.id),
                "data": vinculo.pedido.emissao.isoformat() if vinculo.pedido.emissao else None,
                "fornecedor": getattr(vinculo.pedido.fornecedor, "nome_fornecedor", "") or "",
                "estabelecimento": getattr(vinculo.pedido.loja, "nome_loja", "") or "",
                "quantidade_total": str(sum((item.qtd or 0) for item in vinculo.pedido.itens.all())),
                "valor_total": str(vinculo.pedido.total_pedido or 0),
            }
            for vinculo in pedidos_vinculados
        ],
        "totais": {
            "quantidade_pedido_total": str(pedido_pendente_total if estruturado else pedido_original_total),
            "quantidade_pedido_original_total": str(pedido_original_total),
            "quantidade_pedido_pendente_total": str(pedido_pendente_total),
            "quantidade_nfe_total": str(nfe_total) if nfe_total is not None else None,
            "quantidade_fisica_total": str(fisico_total),
            "diferenca_nfe_pedido": str(nfe_total - (pedido_pendente_total if estruturado else pedido_original_total)) if nfe_total is not None else None,
            "diferenca_fisico_nfe": str(fisico_total - nfe_total) if nfe_total is not None else None,
            "diferenca_fisico_pedido": str(fisico_total - (pedido_pendente_total if estruturado else pedido_original_total)),
        },
        "contagem_operacional": {
            "quantidade_pedidos_vinculados": len(pedidos_vinculados),
            "quantidade_referencias_distintas": len(referencias_recebidas),
            "quantidade_skus_distintos": len(skus_recebidos),
            "quantidade_skus_total_conferencia": len(itens),
            "quantidade_skus_com_divergencia": sum(1 for item in itens if item.diferenca != 0),
            "quantidade_total_fisica_recebida": str(fisico_total),
        },
        "conferencia_sku": linhas,
        "divergencias": {"faltas": faltas, "sobras": sobras},
        "usuario": {
            "id": user.id,
            "username": getattr(user, "username", "") or "",
            "nome_completo": user.get_full_name() if hasattr(user, "get_full_name") else "",
        },
        "observacao": observacao,
    }
    return snapshot, possui_divergencia


def efetivar_estoque_recebimento(recebimento, termo, user):
    existente = RecebimentoMercadoriaEfetivacaoEstoque.objects.filter(recebimento=recebimento).first()
    if existente:
        return existente
    if termo.recebimento_id != recebimento.id or termo.empresa_id != recebimento.empresa_id:
        raise RecebimentoHubError("Termo de recebimento incompatível.")
    if _hash_snapshot(termo.snapshot) != termo.hash_sha256:
        raise RecebimentoHubError("Integridade do termo de recebimento inválida.")
    loja = loja_destino_estoque(recebimento)
    if not loja or loja.empresa_id != recebimento.empresa_id:
        raise RecebimentoHubError("Não foi possível determinar um único estabelecimento para entrada do estoque.")
    linhas = _linhas_fisicas_termo(termo)
    if not linhas:
        raise RecebimentoHubError("Não há quantidade física recebida para efetivar no estoque.")
    custos_por_ean = custos_recebimento_por_ean(recebimento)
    skus = {}
    for linha in linhas:
        sku = ProdutoDetalhe.objects.select_related("produto").filter(ean13=linha["ean"], produto__empresa=recebimento.empresa).first()
        if not sku:
            raise RecebimentoHubError(f"SKU não encontrado para o EAN {linha['ean']}.")
        skus[linha["ean"]] = sku

    quantidade_total = sum(linha["recebido"] for linha in linhas)
    efetivacao = RecebimentoMercadoriaEfetivacaoEstoque.objects.create(
        recebimento=recebimento,
        termo=termo,
        empresa=recebimento.empresa,
        loja=loja,
        efetivado_por=user,
        efetivado_em=timezone.now(),
        quantidade_total=quantidade_total,
        quantidade_skus=len(linhas),
        hash_termo=termo.hash_sha256,
    )
    for linha in linhas:
        sku = skus[linha["ean"]]
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=linha["ean"],
            Idloja=loja,
            defaults={"referencia": sku.produto.referencia or "", "Estoque": Decimal("0"), "reserva": Decimal("0")},
        )
        saldo_anterior = Decimal(estoque.Estoque or 0)
        saldo_posterior = saldo_anterior + linha["recebido"]
        custo_unitario = custos_por_ean.get(linha["ean"], Decimal("0.0000"))
        custo_medio_apos = atualizar_custo_medio_sku(sku, saldo_anterior, linha["recebido"], custo_unitario) if custo_unitario > 0 else q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        estoque.Estoque = saldo_posterior
        if not estoque.referencia:
            estoque.referencia = sku.produto.referencia or ""
        estoque.save(update_fields=["Estoque", "referencia"])
        EstoqueMovimentacao.objects.create(
            Idloja=loja,
            CodigodeBarra=linha["ean"],
            referencia=sku.produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=linha["recebido"],
            custo_unitario=custo_unitario,
            custo_total=q2(linha["recebido"] * custo_unitario),
            custo_medio_apos=custo_medio_apos,
            saldo_anterior=saldo_anterior,
            saldo_posterior=saldo_posterior,
            origem=EstoqueMovimentacao.ORIGEM_RECEBIMENTO_MERCADORIA,
            documento=f"RECEB-{recebimento.id}",
            observacao=f"Recebimento de mercadoria #{recebimento.id} - Termo #{termo.id}",
        )
    pedidos = {v.pedido for v in recebimento.pedidos_vinculados.select_related("pedido").all()}
    for pedido in pedidos:
        sincronizar_atendimento_pedido_compra(pedido)
    if recebimento.xml_fornecedor_id:
        xml = XmlFornecedorRecebido.objects.select_for_update().get(pk=recebimento.xml_fornecedor_id)
        xml.status_operacional = XmlFornecedorRecebido.StatusOperacional.RECEBIDO
        xml.save(update_fields=["status_operacional", "atualizado_em"])
    return efetivacao


def _operador_retaguarda(hub, payload):
    operador_id = payload.get("operador_retaguarda_usuario_id") or payload.get("usuario_retaguarda_id") or payload.get("encerrado_por")
    if not operador_id:
        raise RecebimentoHubError("Informe operador_retaguarda_usuario_id.")
    User = get_user_model()
    operador = User.objects.filter(pk=operador_id, empresa=hub.loja.empresa, is_active=True).first()
    if not operador:
        raise RecebimentoHubError("Operador não pertence à empresa do Hub.")
    return operador


def _aplicar_quantidades_conferencia(recebimento, itens_payload):
    if not isinstance(itens_payload, list) or not itens_payload:
        raise RecebimentoHubError("Informe itens de conferência.")
    conferencias = {
        item.id: item
        for item in recebimento.conferencia_itens.select_for_update().select_related("produto_detalhe").all()
    }
    recebidos = {}
    for row in itens_payload:
        if not isinstance(row, dict):
            raise RecebimentoHubError("Item de conferência inválido.")
        try:
            item_id = int(row.get("conferencia_item_id") or row.get("id"))
        except (TypeError, ValueError) as exc:
            raise RecebimentoHubError("Item de conferência sem identificador.") from exc
        if item_id in recebidos:
            raise RecebimentoHubError("Item de conferência duplicado no payload.")
        conferencia = conferencias.get(item_id)
        if not conferencia:
            raise RecebimentoHubError(f"Item de conferência {item_id} não pertence ao recebimento.")
        sku_id = row.get("sku_retaguarda_id") or row.get("produto_detalhe_id")
        if sku_id and int(sku_id) != conferencia.produto_detalhe_id:
            raise RecebimentoHubError(f"SKU incompatível no item de conferência {item_id}.")
        ean = str(row.get("ean") or "").strip()
        if ean and ean != str(conferencia.produto_detalhe.ean13 or ""):
            raise RecebimentoHubError(f"EAN incompatível no item de conferência {item_id}.")
        quantidade = Decimal(str(row.get("quantidade_recebida", 0) or 0))
        if quantidade < 0:
            raise RecebimentoHubError("Quantidade recebida não pode ser negativa.")
        recebidos[item_id] = quantidade
    if set(recebidos.keys()) != set(conferencias.keys()):
        raise RecebimentoHubError("Payload deve conter todos os itens da conferência.")
    for item_id, quantidade in recebidos.items():
        conferencia = conferencias[item_id]
        conferencia.quantidade_recebida = quantidade
        conferencia.save(update_fields=["quantidade_recebida", "atualizado_em"])


def _quantidades_nfe_por_ean(recebimento):
    xml = recebimento.xml_fornecedor
    if not (
        xml
        and xml.tipo_tratamento == XmlFornecedorRecebido.TipoTratamento.ESTOQUE
        and isinstance(xml.itens_fiscais, list)
        and xml.itens_fiscais
    ):
        return None
    totais = {}
    for item in xml.itens_fiscais:
        quantidade = Decimal(str((item or {}).get("quantidade_comercial") or 0))
        if quantidade <= 0:
            continue
        ean = "".join(ch for ch in str((item or {}).get("gtin_ean") or "") if ch.isdigit())
        if ean:
            totais[ean] = totais.get(ean, Decimal("0")) + quantidade
    return totais


def _linhas_fisicas_termo(termo):
    quantidades_por_ean = {}
    for linha in (termo.snapshot or {}).get("conferencia_sku") or []:
        recebido = Decimal(str(linha.get("recebido") or 0))
        if recebido <= 0:
            continue
        ean = str(linha.get("ean") or "")
        if not ean:
            raise RecebimentoHubError("EAN vazio no termo de recebimento.")
        quantidades_por_ean[ean] = quantidades_por_ean.get(ean, Decimal("0")) + recebido
    return [{"ean": ean, "recebido": recebido} for ean, recebido in quantidades_por_ean.items()]


def _serializar_item_conferencia(item):
    return {
        "conferencia_item_id": item.pk,
        "pedido_id": item.pedido_id,
        "pedido_item_id": item.pedido_item_id,
        "produto_retaguarda_id": item.produto_id,
        "sku_retaguarda_id": item.produto_detalhe_id,
        "referencia": item.produto.referencia or "",
        "descricao": item.produto.descricao or "",
        "cor": {"id": item.cor_id, "nome": item.cor.Descricao or ""},
        "tamanho": {"id": item.tamanho_id, "nome": item.tamanho.Tamanho or ""},
        "ean": item.produto_detalhe.ean13 or "",
        "quantidade_esperada": str(item.quantidade_esperada),
        "quantidade_recebida": str(item.quantidade_recebida),
        "diferenca": str(item.diferenca),
        "situacao": "OK" if item.diferenca == 0 else "SOBRA" if item.diferenca > 0 else "FALTA",
    }


def _loja_payload(loja):
    if not loja:
        return None
    return {"id": loja.pk, "nome": loja.nome_loja, "apelido": loja.apelido_loja}


def _fornecedor_payload(fornecedor):
    if not fornecedor:
        return None
    return {"id": fornecedor.pk, "nome": fornecedor.nome_fornecedor, "documento": fornecedor.documento or fornecedor.cnpj or ""}


def _documento_fiscal_payload(xml):
    if not xml:
        return None
    return {
        "xml_fornecedor_id": xml.pk,
        "modelo": xml.modelo,
        "serie": xml.serie,
        "numero": xml.numero,
        "chave_acesso": xml.chave_acesso,
        "situacao_fiscal": xml.situacao_fiscal,
        "status_operacional": xml.status_operacional,
        "emitente_nome": xml.emitente_nome,
        "emitente_documento": xml.emitente_documento,
        "quantidade_total_faturada": str(xml.quantidade_total_faturada) if xml.quantidade_total_faturada is not None else None,
        "valor_total": str(xml.valor_total or 0),
        "dh_emissao": xml.dh_emissao.isoformat() if xml.dh_emissao else None,
    }


def _termo_payload(termo):
    if not termo:
        return None
    return {"id": termo.pk, "hash_sha256": termo.hash_sha256, "possui_divergencia": termo.possui_divergencia, "encerrado_em": termo.encerrado_em}


def _efetivacao_payload(efetivacao):
    if not efetivacao:
        return None
    return {"id": efetivacao.pk, "quantidade_total": str(efetivacao.quantidade_total), "quantidade_skus": efetivacao.quantidade_skus, "efetivado_em": efetivacao.efetivado_em}


def _resultado_recebimento(recebimento, termo=None, efetivacao=None):
    termo = termo or getattr(recebimento, "termo_encerramento", None)
    efetivacao = efetivacao or getattr(recebimento, "efetivacao_estoque", None)
    return {
        "recebimento_retaguarda_id": recebimento.pk,
        "status": recebimento.status,
        "termo_id": getattr(termo, "pk", None),
        "hash_termo": getattr(termo, "hash_sha256", "") or "",
        "efetivacao_estoque_id": getattr(efetivacao, "pk", None),
    }


def _hash_snapshot(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

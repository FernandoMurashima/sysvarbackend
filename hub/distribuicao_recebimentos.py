from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch

from distribuicao.models import MercadoriaTransito, PedidoVendaDistribuicao
from distribuicao.services import confirmar_recebimento_nota


class DistribuicaoHubError(Exception):
    pass


def recebimentos_distribuicao_hub_queryset(hub, status_transito=None):
    transitos_qs = (
        MercadoriaTransito.objects
        .select_related(
            "pedido_item",
            "pedido_item__produto",
            "pedido_item__sku",
            "sku",
            "sku__produto",
            "sku__idcor",
            "sku__idtamanho",
            "unidade_origem",
            "loja_destino",
            "distribuicao_destino",
        )
        .order_by("id")
    )
    qs = (
        PedidoVendaDistribuicao.objects
        .select_related("empresa", "distribuicao", "unidade_origem", "loja_destino")
        .prefetch_related(Prefetch("transitos", queryset=transitos_qs))
        .filter(empresa=hub.loja.empresa, loja_destino=hub.loja, transitos__isnull=False)
    )
    if status_transito:
        qs = qs.filter(transitos__status=status_transito)
    return qs.distinct().order_by("-nfe_data", "-id")


def obter_recebimento_distribuicao_hub(hub, pedido_id):
    return recebimentos_distribuicao_hub_queryset(hub).filter(pk=pedido_id).first()


def serializar_recebimento_distribuicao_hub(pedido, detalhado=False):
    transitos = list(pedido.transitos.all())
    quantidade_enviada = sum((t.quantidade_enviada or Decimal("0")) for t in transitos)
    quantidade_recebida = sum((t.quantidade_recebida or Decimal("0")) for t in transitos)
    payload = {
        "id": pedido.pk,
        "recebimento_distribuicao_id": pedido.pk,
        "origem": "DISTRIBUICAO",
        "tipo": "DISTRIBUICAO",
        "status": _status_documento(transitos),
        "data_envio": _data_envio(transitos),
        "documento_fiscal": {
            "modelo": "55",
            "numero": pedido.nfe_numero or "",
            "chave_acesso": pedido.nfe_chave or "",
            "status": pedido.nfe_status or "",
            "emitida_em": pedido.nfe_data.isoformat() if pedido.nfe_data else None,
        },
        "pedido_distribuicao": {
            "id": pedido.pk,
            "numero": pedido.numero,
            "status": pedido.status,
            "faturamento_status": pedido.faturamento_status,
            "distribuicao_id": pedido.distribuicao_id,
            "distribuicao_numero": pedido.distribuicao.numero if pedido.distribuicao_id else "",
        },
        "unidade_origem": _loja_payload(pedido.unidade_origem),
        "loja_destino": _loja_payload(pedido.loja_destino),
        "resumo": {
            "quantidade_enviada_total": str(quantidade_enviada),
            "quantidade_recebida_total": str(quantidade_recebida),
            "quantidade_pendente_total": str(quantidade_enviada - quantidade_recebida),
            "quantidade_itens": len(transitos),
        },
        "contrato_retorno": {
            "tipo_evento": "DISTRIBUICAO_LOJA_CONFERIDA",
            "identificador_operacao": "evento_uuid + chave_idempotencia",
            "exige_operador_retaguarda": True,
        },
    }
    if detalhado:
        payload["itens"] = [_serializar_transito_item(transito) for transito in transitos]
    return payload


@transaction.atomic
def processar_retorno_distribuicao_hub(hub, payload):
    pedido_id = _pedido_id_payload(payload)
    if not pedido_id:
        raise DistribuicaoHubError("Informe pedido_distribuicao_id.")

    pedido = (
        PedidoVendaDistribuicao.objects
        .select_for_update()
        .select_related("empresa", "distribuicao", "unidade_origem", "loja_destino")
        .filter(pk=pedido_id, empresa=hub.loja.empresa, loja_destino=hub.loja)
        .first()
    )
    if not pedido:
        raise DistribuicaoHubError("Recebimento de distribuição não encontrado para a loja deste Hub.")

    loja_destino_id = payload.get("loja_destino_id") or payload.get("loja_destino")
    if loja_destino_id not in (None, ""):
        try:
            loja_destino_id = int(loja_destino_id)
        except (TypeError, ValueError) as exc:
            raise DistribuicaoHubError("Loja de destino inválida.") from exc
        if loja_destino_id != hub.loja_id:
            raise DistribuicaoHubError("Loja de destino incompatível com o Hub.")
    _validar_identificacao_documento(pedido, payload)

    _operador_retaguarda(hub, payload)
    transitos = list(
        MercadoriaTransito.objects
        .select_for_update()
        .select_related("pedido", "pedido_item", "loja_destino")
        .filter(pedido=pedido, loja_destino=hub.loja)
        .order_by("id")
    )
    if not transitos:
        raise DistribuicaoHubError("Documento de distribuição sem itens de trânsito.")

    itens_payload = payload.get("itens") or []
    if not isinstance(itens_payload, list) or not itens_payload:
        raise DistribuicaoHubError("Informe os itens da conferência.")

    itens_normalizados = _normalizar_itens_payload(itens_payload)
    ids_esperados = {transito.pk for transito in transitos}
    ids_recebidos = {item["transito"] for item in itens_normalizados}
    if ids_recebidos != ids_esperados:
        raise DistribuicaoHubError("Payload deve conter todos os itens do recebimento de distribuição.")
    _validar_identificacao_itens(transitos, itens_payload)

    fora_transito = [transito.pk for transito in transitos if transito.status != MercadoriaTransito.STATUS_EM_TRANSITO]
    if fora_transito:
        raise DistribuicaoHubError("Recebimento de distribuição já processado ou fora de trânsito.")

    possui_divergencia = any(
        Decimal(str(item["quantidade_recebida"] or 0)) != Decimal(transito.quantidade_enviada or 0)
        for transito in transitos
        for item in itens_normalizados
        if item["transito"] == transito.pk
    )
    observacao = str(payload.get("observacao_divergencia") or payload.get("observacao") or "").strip()
    if possui_divergencia and not observacao:
        raise DistribuicaoHubError("Informe observacao_divergencia para recebimento com divergência.")

    recebidos = confirmar_recebimento_nota(transitos, itens_normalizados)
    pedido.refresh_from_db()
    return _resultado_recebimento_distribuicao(pedido, recebidos)


def mapeamento_recebimento_distribuicao_hub(hub, payload):
    pedido_id = _pedido_id_payload(payload)
    if not pedido_id:
        return {}
    pedido = (
        PedidoVendaDistribuicao.objects
        .select_related("empresa", "distribuicao", "unidade_origem", "loja_destino")
        .prefetch_related("transitos")
        .filter(pk=pedido_id, empresa=hub.loja.empresa, loja_destino=hub.loja)
        .first()
    )
    if not pedido:
        return {}
    return _resultado_recebimento_distribuicao(pedido, list(pedido.transitos.all()))


def _pedido_id_payload(payload):
    return (
        payload.get("pedido_distribuicao_id")
        or payload.get("pedido_id")
        or payload.get("recebimento_distribuicao_id")
        or payload.get("id")
    )


def _normalizar_itens_payload(itens_payload):
    normalizados = []
    vistos = set()
    for row in itens_payload:
        if not isinstance(row, dict):
            raise DistribuicaoHubError("Item de conferência inválido.")
        try:
            transito_id = int(row.get("transito_id") or row.get("transito"))
        except (TypeError, ValueError) as exc:
            raise DistribuicaoHubError("Item de conferência sem transito_id.") from exc
        if transito_id in vistos:
            raise DistribuicaoHubError("Item de trânsito duplicado no payload.")
        vistos.add(transito_id)
        normalizados.append({
            "transito": transito_id,
            "quantidade_recebida": row.get("quantidade_recebida", 0),
        })
    return normalizados


def _validar_identificacao_documento(pedido, payload):
    pedido_numero = str(payload.get("pedido_numero") or payload.get("documento") or "").strip()
    if pedido_numero and pedido_numero != str(pedido.numero):
        raise DistribuicaoHubError("Pedido de distribuição incompatível com o documento informado.")
    nfe_numero = str(payload.get("nfe_numero") or "").strip()
    if nfe_numero and nfe_numero != str(pedido.nfe_numero or ""):
        raise DistribuicaoHubError("NF-e incompatível com o pedido de distribuição.")
    nfe_chave = str(payload.get("nfe_chave") or payload.get("chave_acesso") or "").strip()
    if nfe_chave and nfe_chave != str(pedido.nfe_chave or ""):
        raise DistribuicaoHubError("Chave de acesso incompatível com o pedido de distribuição.")


def _validar_identificacao_itens(transitos, itens_payload):
    transitos_por_id = {transito.pk: transito for transito in transitos}
    for row in itens_payload:
        transito_id = int(row.get("transito_id") or row.get("transito"))
        transito = transitos_por_id.get(transito_id)
        if not transito:
            continue
        sku_id = row.get("sku_retaguarda_id") or row.get("produto_detalhe_id")
        if sku_id not in (None, ""):
            try:
                sku_id = int(sku_id)
            except (TypeError, ValueError) as exc:
                raise DistribuicaoHubError("SKU inválido no item de trânsito.") from exc
            if sku_id != transito.sku_id:
                raise DistribuicaoHubError("SKU incompatível com o item de trânsito.")
        ean = str(row.get("ean") or "").strip()
        if ean and ean != str(transito.ean13 or ""):
            raise DistribuicaoHubError("EAN incompatível com o item de trânsito.")


def _operador_retaguarda(hub, payload):
    operador_id = payload.get("operador_retaguarda_usuario_id") or payload.get("usuario_retaguarda_id") or payload.get("operador_id")
    if not operador_id:
        raise DistribuicaoHubError("Informe operador_retaguarda_usuario_id.")
    User = get_user_model()
    operador = User.objects.filter(pk=operador_id, empresa=hub.loja.empresa, is_active=True).first()
    if not operador:
        raise DistribuicaoHubError("Operador não pertence à empresa do Hub.")
    return operador


def _resultado_recebimento_distribuicao(pedido, transitos):
    transitos = list(transitos)
    return {
        "recebimento_distribuicao_id": pedido.pk,
        "pedido_distribuicao_id": pedido.pk,
        "pedido_numero": pedido.numero,
        "nfe_numero": pedido.nfe_numero or "",
        "nfe_chave": pedido.nfe_chave or "",
        "status": _status_documento(transitos),
        "itens_processados": len(transitos),
        "quantidade_enviada_total": str(sum((t.quantidade_enviada or Decimal("0")) for t in transitos)),
        "quantidade_recebida_total": str(sum((t.quantidade_recebida or Decimal("0")) for t in transitos)),
    }


def _serializar_transito_item(transito):
    item = transito.pedido_item
    sku = transito.sku
    return {
        "transito_id": transito.pk,
        "pedido_item_id": transito.pedido_item_id,
        "produto_retaguarda_id": item.produto_id,
        "sku_retaguarda_id": transito.sku_id,
        "ean": transito.ean13 or "",
        "referencia": item.referencia or "",
        "descricao": item.descricao or "",
        "cor": item.cor_descricao or getattr(getattr(sku, "idcor", None), "Descricao", "") or "",
        "tamanho": item.tamanho_descricao or getattr(getattr(sku, "idtamanho", None), "Tamanho", "") or "",
        "quantidade_enviada": str(transito.quantidade_enviada),
        "quantidade_recebida": str(transito.quantidade_recebida),
        "quantidade_divergente": str(transito.quantidade_divergente),
        "status": transito.status,
    }


def _status_documento(transitos):
    if not transitos:
        return ""
    statuses = {transito.status for transito in transitos}
    if MercadoriaTransito.STATUS_DIVERGENTE in statuses:
        return MercadoriaTransito.STATUS_DIVERGENTE
    if MercadoriaTransito.STATUS_EM_TRANSITO in statuses:
        return MercadoriaTransito.STATUS_EM_TRANSITO
    if statuses == {MercadoriaTransito.STATUS_RECEBIDA}:
        return MercadoriaTransito.STATUS_RECEBIDA
    if statuses == {MercadoriaTransito.STATUS_AGUARDANDO_EXPEDICAO}:
        return MercadoriaTransito.STATUS_AGUARDANDO_EXPEDICAO
    return "MISTO"


def _data_envio(transitos):
    datas = [transito.data_envio for transito in transitos if transito.data_envio]
    data = min(datas) if datas else None
    return data.isoformat() if data else None


def _loja_payload(loja):
    if not loja:
        return None
    return {"id": loja.pk, "nome": loja.nome_loja, "apelido": loja.apelido_loja}

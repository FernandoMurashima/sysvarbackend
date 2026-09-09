from decimal import Decimal

from django.db.models import Max, Sum
from django.utils import timezone

from compras.models import PedidoCompraEntrega
from fiscal.models import RecebimentoMercadoriaConferenciaItem


def quantidades_fisicas_efetivadas_por_sku(pedido_itens, empresa_id, excluir_recebimento_id=None):
    qs = RecebimentoMercadoriaConferenciaItem.objects.filter(
        pedido_item__in=pedido_itens,
        recebimento__efetivacao_estoque__isnull=False,
        recebimento__empresa_id=empresa_id,
    )
    if excluir_recebimento_id:
        qs = qs.exclude(recebimento_id=excluir_recebimento_id)
    return {
        (row["pedido_item_id"], row["produto_detalhe_id"]): Decimal(row["total"] or 0)
        for row in qs.values("pedido_item_id", "produto_detalhe_id").annotate(total=Sum("quantidade_recebida"))
    }


def sincronizar_atendimento_pedido_compra(pedido):
    itens = list(pedido.itens.all().order_by("id"))
    if not itens:
        return {"status_pedido": pedido.status, "itens_atualizados": 0, "itens_atendidos": 0}

    efetivados_por_sku = quantidades_fisicas_efetivadas_por_sku(itens, pedido.empresa_id)
    recebidos = {
        item.pk: {
            "total": sum(total for (pedido_item_id, _sku_id), total in efetivados_por_sku.items() if pedido_item_id == item.pk),
            "ultimo_recebimento": None,
        }
        for item in itens
    }
    ultimos = {
        row["pedido_item_id"]: row["ultimo_recebimento"]
        for row in (
            RecebimentoMercadoriaConferenciaItem.objects.filter(
                pedido_item__in=itens,
                recebimento__efetivacao_estoque__isnull=False,
                recebimento__empresa_id=pedido.empresa_id,
            )
            .values("pedido_item_id")
            .annotate(ultimo_recebimento=Max("recebimento__efetivacao_estoque__efetivado_em"))
        )
    }
    for pedido_item_id, ultimo in ultimos.items():
        recebidos.setdefault(pedido_item_id, {"total": Decimal("0"), "ultimo_recebimento": None})
        recebidos[pedido_item_id]["ultimo_recebimento"] = ultimo

    atendidos = 0
    parciais = 0
    atualizados = 0
    for item in itens:
        prevista = Decimal(item.qtd or 0)
        info = recebidos.get(item.pk) or {}
        recebida = Decimal(info.get("total") or 0)
        entrega = item.entregas.order_by("id").first()
        if not entrega:
            entrega = PedidoCompraEntrega(item=item)

        entrega.qtd_prevista = prevista
        entrega.data_prevista = pedido.previsao_entrega
        entrega.qtd_recebida = recebida
        if recebida <= 0:
            entrega.status = "PREV"
            entrega.data_recebida = None
        elif recebida < prevista:
            entrega.status = "PARC"
            entrega.data_recebida = None
            parciais += 1
        else:
            entrega.status = "RECB"
            ultimo = info.get("ultimo_recebimento")
            entrega.data_recebida = timezone.localtime(ultimo).date() if ultimo else timezone.localdate()
            atendidos += 1
        entrega.save()
        atualizados += 1

    if pedido.status != "CA":
        novo_status = "AT" if atendidos == len(itens) else "AP"
        if pedido.status != novo_status:
            pedido.status = novo_status
            pedido.save(update_fields=["status"])

    return {
        "status_pedido": pedido.status,
        "itens_atualizados": atualizados,
        "itens_atendidos": atendidos,
        "itens_parciais": parciais,
    }

from decimal import Decimal

from django.db.models import Max, Sum
from django.utils import timezone

from compras.models import PedidoCompraEntrega
from fiscal.models import RecebimentoMercadoriaConferenciaItem


def sincronizar_atendimento_pedido_compra(pedido):
    itens = list(pedido.itens.all().order_by("id"))
    if not itens:
        return {"status_pedido": pedido.status, "itens_atualizados": 0, "itens_atendidos": 0}

    recebidos = {
        row["pedido_item_id"]: row
        for row in RecebimentoMercadoriaConferenciaItem.objects.filter(
            pedido_item__in=itens,
            recebimento__efetivacao_estoque__isnull=False,
            recebimento__empresa_id=pedido.empresa_id,
        )
        .values("pedido_item_id")
        .annotate(
            total=Sum("quantidade_recebida"),
            ultimo_recebimento=Max("recebimento__efetivacao_estoque__efetivado_em"),
        )
    }

    atendidos = 0
    parciais = 0
    atualizados = 0
    for item in itens:
        prevista = Decimal(item.qtd or 0)
        info = recebidos.get(item.id) or {}
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

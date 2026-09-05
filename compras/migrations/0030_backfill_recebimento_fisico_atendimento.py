from decimal import Decimal

from django.db import migrations
from django.db.models import Max, Sum
from django.utils import timezone


def sincronizar_recebimentos_fisicos(apps, schema_editor):
    PedidoCompra = apps.get_model("compras", "PedidoCompra")
    PedidoCompraEntrega = apps.get_model("compras", "PedidoCompraEntrega")
    ConferenciaItem = apps.get_model("fiscal", "RecebimentoMercadoriaConferenciaItem")
    pedidos_ids = (
        ConferenciaItem.objects
        .filter(recebimento__efetivacao_estoque__isnull=False)
        .values_list("pedido_id", flat=True)
        .distinct()
    )
    for pedido in PedidoCompra.objects.filter(id__in=pedidos_ids).prefetch_related("itens__entregas"):
        itens = list(pedido.itens.all().order_by("id"))
        if not itens:
            continue
        recebidos = {
            row["pedido_item_id"]: row
            for row in ConferenciaItem.objects.filter(
                pedido_item__in=itens,
                recebimento__efetivacao_estoque__isnull=False,
                recebimento__empresa_id=pedido.empresa_id,
            )
            .values("pedido_item_id")
            .annotate(total=Sum("quantidade_recebida"), ultimo_recebimento=Max("recebimento__efetivacao_estoque__efetivado_em"))
        }
        atendidos = 0
        for item in itens:
            prevista = Decimal(item.qtd or 0)
            info = recebidos.get(item.id) or {}
            recebida = Decimal(info.get("total") or 0)
            entrega = item.entregas.order_by("id").first() or PedidoCompraEntrega(item=item)
            entrega.qtd_prevista = prevista
            entrega.data_prevista = pedido.previsao_entrega
            entrega.qtd_recebida = recebida
            if recebida <= 0:
                entrega.status = "PREV"
                entrega.data_recebida = None
            elif recebida < prevista:
                entrega.status = "PARC"
                entrega.data_recebida = None
            else:
                entrega.status = "RECB"
                ultimo = info.get("ultimo_recebimento")
                entrega.data_recebida = timezone.localtime(ultimo).date() if ultimo else timezone.localdate()
                atendidos += 1
            entrega.save()
        if pedido.status != "CA":
            novo_status = "AT" if atendidos == len(itens) else "AP"
            if pedido.status != novo_status:
                pedido.status = novo_status
                pedido.save(update_fields=["status"])


class Migration(migrations.Migration):
    dependencies = [
        ("compras", "0029_requisicaosetor_centro_custo"),
        ("fiscal", "0035_recebimentomercadoriaefetivacaoestoque"),
    ]

    operations = [
        migrations.RunPython(sincronizar_recebimentos_fisicos, migrations.RunPython.noop),
    ]

from decimal import Decimal

from django.db import migrations


def recalcular_pedidos_atendidos(apps, schema_editor):
    PedidoCompra = apps.get_model("compras", "PedidoCompra")
    PedidoCompraEntrega = apps.get_model("compras", "PedidoCompraEntrega")
    NotaFiscalEntradaItem = apps.get_model("fiscal", "NotaFiscalEntradaItem")

    pedidos = PedidoCompra.objects.exclude(status="CA").prefetch_related("itens__entregas")
    for pedido in pedidos:
        itens = list(pedido.itens.all())
        if not itens:
            continue

        atendidos = 0
        for item in itens:
            prevista = Decimal(item.qtd or 0)
            recebida = Decimal("0")
            recebimentos = NotaFiscalEntradaItem.objects.filter(
                pedido_item_id=item.pk,
                nota__pedido_compra_id=pedido.pk,
                nota__status="FE",
            )
            for recebimento in recebimentos:
                recebida += Decimal(recebimento.qtd_recebida or 0)

            entrega = item.entregas.order_by("id").first()
            if not entrega:
                entrega = PedidoCompraEntrega(item=item, qtd_prevista=prevista, data_prevista=pedido.previsao_entrega)

            entrega.qtd_prevista = prevista
            entrega.qtd_recebida = recebida
            if prevista > 0 and recebida >= prevista:
                entrega.status = "RECB"
                entrega.data_recebida = None
                atendidos += 1
            elif recebida > 0:
                entrega.status = "PARC"
                entrega.data_recebida = None
            else:
                entrega.status = "PREV"
                entrega.data_recebida = None
            entrega.save()

        novo_status = "AT" if atendidos == len(itens) else "AP"
        if pedido.status in ("AP", "AT") and pedido.status != novo_status:
            pedido.status = novo_status
            pedido.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0004_pedidocompra_status_atendido"),
        ("fiscal", "0008_vendadevolucaoitem_cmv_total_and_more"),
    ]

    operations = [
        migrations.RunPython(recalcular_pedidos_atendidos, migrations.RunPython.noop),
    ]

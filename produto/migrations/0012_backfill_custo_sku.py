from decimal import Decimal

from django.db import migrations


def backfill_custos(apps, schema_editor):
    NotaFiscalEntradaItem = apps.get_model("fiscal", "NotaFiscalEntradaItem")
    PackItem = apps.get_model("produto", "PackItem")
    ProdutoDetalhe = apps.get_model("produto", "ProdutoDetalhe")
    VendaPdvItem = apps.get_model("fiscal", "VendaPdvItem")

    itens_nf = (
        NotaFiscalEntradaItem.objects
        .filter(nota__status="FE", pedido_item__produto__isnull=False, pedido_item__cor__isnull=False, pedido_item__pack__isnull=False)
        .order_by("nota__dt_entrada", "id")
    )

    for item_nf in itens_nf:
        pedido_item = item_nf.pedido_item
        custo = Decimal(item_nf.preco_unit_nf or 0)
        if custo <= 0:
            continue
        for pack_item in PackItem.objects.filter(pack_id=pedido_item.pack_id):
            sku = (
                ProdutoDetalhe.objects
                .filter(
                    produto_id=pedido_item.produto_id,
                    idcor_id=pedido_item.cor_id,
                    idtamanho_id=pack_item.tamanho_id,
                )
                .first()
            )
            if not sku:
                continue
            updates = {"custo_ultima_compra": custo}
            if not Decimal(sku.custo_original or 0):
                updates["custo_original"] = custo
            ProdutoDetalhe.objects.filter(pk=sku.pk).update(**updates)

    for item in VendaPdvItem.objects.filter(custo_unitario=0).select_related("sku"):
        custo = Decimal(item.sku.custo_ultima_compra or item.sku.custo_original or 0)
        if custo <= 0:
            continue
        cmv_total = Decimal(item.quantidade or 0) * custo
        VendaPdvItem.objects.filter(pk=item.pk).update(custo_unitario=custo, cmv_total=cmv_total)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0011_produtodetalhe_custo_original_and_more"),
        ("fiscal", "0007_vendapdvitem_cmv_total_vendapdvitem_custo_unitario"),
    ]

    operations = [
        migrations.RunPython(backfill_custos, noop),
    ]

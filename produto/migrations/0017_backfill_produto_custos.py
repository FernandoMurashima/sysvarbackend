from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


def _q4(value):
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def backfill_produto_custos(apps, schema_editor):
    Produto = apps.get_model("produto", "Produto")
    NotaFiscalEntradaItem = apps.get_model("fiscal", "NotaFiscalEntradaItem")

    itens = (
        NotaFiscalEntradaItem.objects
        .filter(
            nota__status="FE",
            pedido_item__produto__tipo_produto__in=["2", "4"],
            qtd_recebida__gt=0,
        )
        .select_related("pedido_item__produto", "nota")
        .order_by("nota__dt_entrada", "id")
    )

    custos_por_produto = {}
    for item in itens:
        total = Decimal(item.total_item or 0)
        qtd = Decimal(item.qtd_recebida or 0)
        if qtd <= 0:
            continue
        custo = _q4((total / qtd) if total > 0 else item.preco_unit_nf)
        if custo > 0:
            custos_por_produto[item.pedido_item.produto_id] = custo

    for produto_id, custo in custos_por_produto.items():
        produto = Produto.objects.filter(pk=produto_id).first()
        if not produto:
            continue
        updates = {
            "custo_ultima_compra": custo,
            "custo_medio": custo,
        }
        if not Decimal(produto.custo_original or 0):
            updates["custo_original"] = custo
        Produto.objects.filter(pk=produto_id).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0016_produto_custos"),
        ("fiscal", "0008_vendadevolucaoitem_cmv_total_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_produto_custos, migrations.RunPython.noop),
    ]

from django.db import migrations, models
import django.db.models.deletion


def preencher_empresas(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    PedidoCompra = apps.get_model("compras", "PedidoCompra")

    empresa_padrao = Empresa.objects.order_by("id").first()
    if not empresa_padrao:
        return

    for pedido in PedidoCompra.objects.filter(empresa__isnull=True).select_related("loja", "fornecedor"):
        pedido.empresa_id = (
            getattr(pedido.loja, "empresa_id", None)
            or getattr(pedido.fornecedor, "empresa_id", None)
            or empresa_padrao.id
        )
        pedido.save(update_fields=["empresa"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0006_cliente_fornecedor_funcionario_empresa"),
        ("compras", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedidocompra",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pedidos_compra", to="cadastros.empresa"),
        ),
        migrations.RunPython(preencher_empresas, migrations.RunPython.noop),
    ]

from django.db import migrations, models
import django.db.models.deletion


def preencher_empresas(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    VendaPdv = apps.get_model("fiscal", "VendaPdv")
    VendaDevolucao = apps.get_model("fiscal", "VendaDevolucao")

    empresa_padrao = Empresa.objects.order_by("id").first()
    if not empresa_padrao:
        return

    for venda in VendaPdv.objects.filter(empresa__isnull=True).select_related("loja", "cliente"):
        venda.empresa_id = (
            getattr(venda.loja, "empresa_id", None)
            or getattr(venda.cliente, "empresa_id", None)
            or empresa_padrao.id
        )
        venda.save(update_fields=["empresa"])

    for devolucao in VendaDevolucao.objects.filter(empresa__isnull=True).select_related("venda", "loja", "cliente"):
        devolucao.empresa_id = (
            getattr(devolucao.venda, "empresa_id", None)
            or getattr(devolucao.loja, "empresa_id", None)
            or getattr(devolucao.cliente, "empresa_id", None)
            or empresa_padrao.id
        )
        devolucao.save(update_fields=["empresa"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0006_cliente_fornecedor_funcionario_empresa"),
        ("fiscal", "0005_vendadevolucao_vendadevolucaoitem_nfe_devolucao"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendapdv",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="vendas_pdv", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="vendadevolucao",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="devolucoes_venda", to="cadastros.empresa"),
        ),
        migrations.RunPython(preencher_empresas, migrations.RunPython.noop),
    ]

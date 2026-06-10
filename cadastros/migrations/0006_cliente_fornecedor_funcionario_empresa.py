from django.db import migrations, models
import django.db.models.deletion


def vincular_empresa_cadastros(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Loja = apps.get_model("cadastros", "Loja")
    Cliente = apps.get_model("cadastros", "Cliente")
    Fornecedor = apps.get_model("cadastros", "Fornecedor")
    Funcionarios = apps.get_model("cadastros", "Funcionarios")

    empresa_padrao = Empresa.objects.order_by("id").first()
    if not empresa_padrao:
        empresa_padrao = Empresa.objects.create(nome="CISVAR Base Atual", nome_fantasia="CISVAR Base Atual")

    Cliente.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    Fornecedor.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)

    for funcionario in Funcionarios.objects.select_related("idloja").filter(empresa__isnull=True):
        funcionario.empresa = getattr(funcionario.idloja, "empresa", None) or empresa_padrao
        funcionario.save(update_fields=["empresa"])

    for loja in Loja.objects.filter(empresa__isnull=True):
        loja.empresa = empresa_padrao
        loja.save(update_fields=["empresa"])


def desvincular_empresa_cadastros(apps, schema_editor):
    Cliente = apps.get_model("cadastros", "Cliente")
    Fornecedor = apps.get_model("cadastros", "Fornecedor")
    Funcionarios = apps.get_model("cadastros", "Funcionarios")
    Cliente.objects.update(empresa=None)
    Fornecedor.objects.update(empresa=None)
    Funcionarios.objects.update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0005_empresa_loja_empresa"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clientes",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="fornecedores",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="funcionarios",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="funcionarios",
                to="cadastros.empresa",
            ),
        ),
        migrations.RunPython(vincular_empresa_cadastros, desvincular_empresa_cadastros),
    ]

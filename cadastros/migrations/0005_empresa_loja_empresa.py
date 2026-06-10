from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def criar_empresa_padrao(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Loja = apps.get_model("cadastros", "Loja")

    empresa, _ = Empresa.objects.get_or_create(
        nome="CISVAR Base Atual",
        defaults={
            "nome_fantasia": "CISVAR Base Atual",
            "ativo": True,
        },
    )
    Loja.objects.filter(empresa__isnull=True).update(empresa=empresa)


def desfazer_empresa_padrao(apps, schema_editor):
    Loja = apps.get_model("cadastros", "Loja")
    Loja.objects.update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0004_funcionarios_comissao_percentual"),
    ]

    operations = [
        migrations.CreateModel(
            name="Empresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(db_index=True, max_length=120)),
                ("nome_fantasia", models.CharField(blank=True, db_index=True, max_length=120, null=True)),
                ("documento", models.CharField(blank=True, max_length=18, null=True, unique=True)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["nome"],
                "indexes": [
                    models.Index(fields=["nome"], name="cadastros_e_nome_c3e4c1_idx"),
                    models.Index(fields=["nome_fantasia"], name="cadastros_e_nome_fa_5de0ca_idx"),
                    models.Index(fields=["ativo"], name="cadastros_e_ativo_04efdd_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="loja",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lojas",
                to="cadastros.empresa",
            ),
        ),
        migrations.RunPython(criar_empresa_padrao, desfazer_empresa_padrao),
    ]

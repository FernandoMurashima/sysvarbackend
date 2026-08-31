from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0028_corrige_comissao_gerente_supervisor"),
    ]

    operations = [
        migrations.CreateModel(
            name="CentroCusto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=30)),
                ("descricao", models.CharField(max_length=120)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("data_atualizacao", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="centros_custo", to="cadastros.empresa")),
            ],
            options={
                "ordering": ["codigo"],
                "indexes": [
                    models.Index(fields=["empresa", "ativo"], name="idx_ccusto_empresa_ativo"),
                    models.Index(fields=["empresa", "descricao"], name="idx_ccusto_empresa_desc"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("empresa", "codigo"), name="uq_empresa_centro_custo_codigo"),
                ],
            },
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0030_loja_codigo_municipio_ibge"),
        ("financeiro", "0034_sequencia_documento_escopo"),
    ]

    operations = [
        migrations.AddField(
            model_name="formapagamento",
            name="permite_parcelamento",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="FormaPagamentoCondicao",
            fields=[
                ("Idformapagamentocondicao", models.BigAutoField(primary_key=True, serialize=False)),
                ("taxa_percentual", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("taxa_fixa", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("ativo", models.BooleanField(default=True)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "empresa",
                    models.ForeignKey(
                        blank=True,
                        db_index=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="formas_pagamento_condicoes",
                        to="cadastros.empresa",
                    ),
                ),
                (
                    "forma_pagamento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="condicoes_parcelamento",
                        to="financeiro.formapagamento",
                    ),
                ),
                (
                    "prazo_pagamento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="formas_pagamento_condicoes",
                        to="financeiro.prazopagamento",
                    ),
                ),
            ],
            options={
                "db_table": "financeiro_forma_pagamento_condicao",
                "ordering": ["forma_pagamento__codigo", "prazo_pagamento__num_parcelas", "prazo_pagamento__codigo"],
            },
        ),
        migrations.AddConstraint(
            model_name="formapagamentocondicao",
            constraint=models.UniqueConstraint(
                fields=("forma_pagamento", "prazo_pagamento"),
                name="uq_forma_pagamento_condicao_prazo",
            ),
        ),
        migrations.AddIndex(
            model_name="formapagamentocondicao",
            index=models.Index(fields=["empresa", "ativo"], name="financeiro__empresa_7a0a69_idx"),
        ),
        migrations.AddIndex(
            model_name="formapagamentocondicao",
            index=models.Index(fields=["forma_pagamento", "ativo"], name="financeiro__forma_a_0f2e86_idx"),
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0025_rename_prazo_pagamento_parcela_index"),
        ("compras", "0020_cotacao_cancelado_em_cotacao_cancelado_por_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cotacaoproposta",
            name="prazo_entrega_dias",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cotacaoproposta",
            name="prazo_pagamento",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cotacoes_propostas",
                to="financeiro.prazopagamento",
            ),
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0025_rename_prazo_pagamento_parcela_index"),
        ("compras", "0005_recalcula_pedidos_atendidos"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedidocompra",
            name="prazo_pagamento",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pedidos_compra", to="financeiro.prazopagamento"),
        ),
    ]

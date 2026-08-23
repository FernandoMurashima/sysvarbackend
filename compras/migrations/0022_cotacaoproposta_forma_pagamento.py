from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0021_cotacao_proposta_pagamento_prazo"),
    ]

    operations = [
        migrations.AddField(
            model_name="cotacaoproposta",
            name="forma_pagamento",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]

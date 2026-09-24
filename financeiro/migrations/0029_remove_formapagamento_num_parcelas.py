from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0028_remover_forma_pagamento_parcela"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="formapagamento",
            name="num_parcelas",
        ),
    ]

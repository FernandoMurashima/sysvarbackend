from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0006_pedidocompra_prazo_pagamento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedidocompra',
            name='tipo',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Não definido'),
                    ('1', 'Revenda'),
                    ('2', 'Uso/Consumo'),
                    ('4', 'Insumo'),
                ],
                default='',
                max_length=1,
            ),
        ),
    ]

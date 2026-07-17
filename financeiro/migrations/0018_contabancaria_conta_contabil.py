from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0017_formas_pagamento_prazos_basicos'),
    ]

    operations = [
        migrations.AddField(
            model_name='contabancaria',
            name='conta_contabil',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0015_fichatecnica_fichatecnicaitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='produto',
            name='custo_medio',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='produto',
            name='custo_original',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='produto',
            name='custo_ultima_compra',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=12),
        ),
    ]

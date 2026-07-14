from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0020_unidade_permite_decimal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='estoque',
            name='Estoque',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AlterField(
            model_name='estoque',
            name='reserva',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AlterField(
            model_name='estoquemovimentacao',
            name='quantidade',
            field=models.DecimalField(decimal_places=3, max_digits=14),
        ),
        migrations.AlterField(
            model_name='estoquemovimentacao',
            name='saldo_anterior',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='estoquemovimentacao',
            name='saldo_posterior',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='inventarioestoqueitem',
            name='diferenca',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='inventarioestoqueitem',
            name='saldo_contado',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='inventarioestoqueitem',
            name='saldo_sistema',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=14),
        ),
    ]

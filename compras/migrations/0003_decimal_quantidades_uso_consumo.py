from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0002_pedidocompra_empresa'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedidocompraitem',
            name='qtd',
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                max_digits=12,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AlterField(
            model_name='pedidocompraentrega',
            name='qtd_prevista',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='pedidocompraentrega',
            name='qtd_recebida',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
    ]

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0007_unifica_tipo_pedido_compra"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pedidocompra",
            name="emissao",
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]

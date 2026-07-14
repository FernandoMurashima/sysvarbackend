from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0003_decimal_quantidades_uso_consumo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pedidocompra",
            name="status",
            field=models.CharField(
                choices=[
                    ("AB", "Aberto"),
                    ("AP", "Aprovado"),
                    ("AT", "Atendido"),
                    ("CA", "Cancelado"),
                ],
                default="AB",
                max_length=2,
            ),
        ),
    ]

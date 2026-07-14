from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0013_estoquemovimentacao_custo_medio_apos_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="produto",
            name="tipo_produto",
            field=models.CharField(
                choices=[
                    ("1", "Revenda"),
                    ("2", "Uso/Consumo"),
                    ("3", "Produto Próprio"),
                    ("4", "Insumo de Produção"),
                ],
                default="1",
                max_length=1,
            ),
        ),
    ]

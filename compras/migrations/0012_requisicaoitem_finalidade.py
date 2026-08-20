from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0011_requisicao_setor_structured"),
    ]

    operations = [
        migrations.AddField(
            model_name="requisicaoitem",
            name="finalidade",
            field=models.CharField(
                blank=True,
                choices=[
                    ("USO_CONSUMO", "Uso e Consumo"),
                    ("ALMOXARIFADO", "Estoque/Almoxarifado"),
                    ("IMOBILIZADO", "Imobilizado"),
                    ("OUTRO", "Outro"),
                ],
                default="",
                max_length=20,
            ),
        ),
    ]

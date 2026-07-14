from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0011_empresa_modulos_producao"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fornecedor",
            name="categoria",
            field=models.CharField(
                blank=True,
                choices=[
                    ("MATERIA_PRIMA", "Matéria-prima"),
                    ("AVIAMENTO", "Aviamento"),
                    ("REVENDA", "Produto de revenda"),
                    ("FACCAO", "Facção"),
                    ("PRESTADOR", "Prestador de serviço"),
                    ("TRANSPORTADORA", "Transportadora"),
                    ("OUTROS", "Outros"),
                ],
                db_index=True,
                max_length=15,
                null=True,
            ),
        ),
    ]

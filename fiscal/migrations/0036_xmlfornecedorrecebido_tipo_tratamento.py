from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0035_recebimentomercadoriaefetivacaoestoque"),
    ]

    operations = [
        migrations.AddField(
            model_name="xmlfornecedorrecebido",
            name="tipo_tratamento",
            field=models.CharField(
                choices=[
                    ("NAO_DEFINIDO", "Não definido"),
                    ("ESTOQUE", "Mercadoria para estoque"),
                    ("USO_CONSUMO", "Uso e consumo"),
                    ("INSUMO_PRODUCAO", "Insumo / produção"),
                    ("FISCAL_SEM_ESTOQUE", "Entrada fiscal sem estoque"),
                ],
                db_index=True,
                default="NAO_DEFINIDO",
                max_length=24,
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0037_xmlfornecedorrecebido_dados_itens_fiscais"),
    ]

    operations = [
        migrations.AlterField(
            model_name="xmlfornecedorrecebido",
            name="quantidade_total_faturada",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=15, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0032_recebimentomercadoriaconferenciaitem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="xmlfornecedorrecebido",
            name="quantidade_total_faturada",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="xmlfornecedorrecebido",
            name="unidade_comercial",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
    ]

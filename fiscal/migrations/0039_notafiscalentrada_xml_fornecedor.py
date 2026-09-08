from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0038_alter_xmlfornecedorrecebido_quantidade_total_faturada"),
    ]

    operations = [
        migrations.AddField(
            model_name="notafiscalentrada",
            name="xml_fornecedor",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="nota_fiscal_entrada",
                to="fiscal.xmlfornecedorrecebido",
            ),
        ),
    ]

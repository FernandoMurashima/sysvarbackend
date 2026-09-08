from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0036_xmlfornecedorrecebido_tipo_tratamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="xmlfornecedorrecebido",
            name="dados_fiscais",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="xmlfornecedorrecebido",
            name="itens_fiscais",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

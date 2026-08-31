from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0029_centrocusto"),
        ("compras", "0028_cotacaoitem_ordem_servico_material_origem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="requisicaosetor",
            name="centro_custo",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="setores_requisicao", to="cadastros.centrocusto"),
        ),
    ]

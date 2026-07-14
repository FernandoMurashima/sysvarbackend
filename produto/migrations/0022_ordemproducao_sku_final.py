from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0021_decimal_estoque_quantidades'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordemproducao',
            name='sku_final',
            field=models.ForeignKey(
                blank=True,
                help_text='SKU acabado produzido pela OP. A entrada ocorre no estoque central da empresa.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='ordens_producao',
                to='produto.produtodetalhe',
            ),
        ),
        migrations.AddIndex(
            model_name='ordemproducao',
            index=models.Index(fields=['sku_final'], name='produto_ord_sku_fin_b68bea_idx'),
        ),
    ]

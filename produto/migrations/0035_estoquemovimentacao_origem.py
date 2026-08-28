from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0034_produto_fornecedor_conversao_unidade'),
    ]

    operations = [
        migrations.AddField(
            model_name='estoquemovimentacao',
            name='origem',
            field=models.CharField(
                blank=True,
                choices=[
                    ('NFE', 'NF-e'),
                    ('VENDA', 'Venda'),
                    ('DEVOLUCAO', 'Devolução'),
                    ('TRANSFERENCIA', 'Transferência'),
                    ('INVENTARIO', 'Inventário'),
                    ('PRODUCAO', 'Produção'),
                    ('AJUSTE_MANUAL', 'Ajuste manual'),
                ],
                db_index=True,
                default='',
                max_length=20,
            ),
        ),
    ]

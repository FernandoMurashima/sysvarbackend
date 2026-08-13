from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0029_produtousoconsumoestoque_produtousoconsumohistorico_and_more'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='produto',
            name='produto_pro_control_c48a3e_idx',
        ),
        migrations.RemoveField(
            model_name='produto',
            name='controla_estoque',
        ),
        migrations.RemoveConstraint(
            model_name='produtousoconsumoestoque',
            name='uq_uso_consumo_estoque_empresa_produto_matriz',
        ),
        migrations.RemoveIndex(
            model_name='produtousoconsumoestoque',
            name='produto_uso_loja_ma_dadcab_idx',
        ),
        migrations.RenameField(
            model_name='produtousoconsumoestoque',
            old_name='loja_matriz',
            new_name='loja',
        ),
        migrations.RenameField(
            model_name='produtousoconsumomovimentacao',
            old_name='loja_matriz',
            new_name='loja',
        ),
        migrations.AddIndex(
            model_name='produtousoconsumoestoque',
            index=models.Index(fields=['loja'], name='produto_uso_loja_id_3aef6e_idx'),
        ),
        migrations.AddConstraint(
            model_name='produtousoconsumoestoque',
            constraint=models.UniqueConstraint(fields=('empresa', 'produto', 'loja'), name='uq_uso_estoque_emp_prod_loja'),
        ),
    ]

# Generated manually for Sysvar promotion support.

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0004_funcionarios_comissao_percentual'),
        ('produto', '0007_inventarioestoque_estoquemovimentacao_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Promocao',
            fields=[
                ('Idpromocao', models.BigAutoField(primary_key=True, serialize=False)),
                ('nome', models.CharField(max_length=100)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('data_inicio', models.DateField(db_index=True)),
                ('data_fim', models.DateField(blank=True, db_index=True, null=True)),
                ('tipo', models.CharField(choices=[('DESCONTO_PERCENTUAL', 'Desconto percentual'), ('DESCONTO_VALOR', 'Desconto em valor'), ('PRECO_FIXO', 'Preço fixo')], max_length=25)),
                ('valor', models.DecimalField(decimal_places=4, max_digits=18)),
                ('escopo', models.CharField(choices=[('TODOS', 'Todos os produtos'), ('PRODUTO', 'Produto'), ('COLECAO', 'Coleção'), ('GRUPO', 'Grupo'), ('SUBGRUPO', 'Subgrupo')], default='TODOS', max_length=15)),
                ('prioridade', models.PositiveIntegerField(default=10)),
                ('acumula_cashback', models.BooleanField(default=True)),
                ('observacao', models.CharField(blank=True, default='', max_length=255)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('colecoes', models.ManyToManyField(blank=True, related_name='promocoes', to='produto.colecao')),
                ('grupos', models.ManyToManyField(blank=True, related_name='promocoes', to='produto.grupo')),
                ('lojas', models.ManyToManyField(blank=True, related_name='promocoes', to='cadastros.loja')),
                ('produtos', models.ManyToManyField(blank=True, related_name='promocoes', to='produto.produto')),
                ('subgrupos', models.ManyToManyField(blank=True, related_name='promocoes', to='produto.subgrupo')),
            ],
            options={
                'db_table': 'produto_promocao',
                'ordering': ['-ativo', '-data_inicio', 'prioridade', 'nome'],
                'indexes': [
                    models.Index(fields=['ativo', 'data_inicio', 'data_fim'], name='produto_pro_ativo_571397_idx'),
                    models.Index(fields=['escopo', 'prioridade'], name='produto_pro_escopo_d49f2a_idx'),
                ],
            },
        ),
    ]

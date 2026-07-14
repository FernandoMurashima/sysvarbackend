import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0012_alter_fornecedor_categoria'),
        ('produto', '0017_backfill_produto_custos'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrdemProducao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero', models.CharField(db_index=True, max_length=30)),
                ('quantidade', models.DecimalField(decimal_places=3, max_digits=12)),
                ('rendimento', models.DecimalField(decimal_places=3, default=1, max_digits=10)),
                ('status', models.CharField(choices=[('ABERTA', 'Aberta'), ('APROVADA', 'Aprovada'), ('EM_PRODUCAO', 'Em produção'), ('FINALIZADA', 'Finalizada'), ('CANCELADA', 'Cancelada')], db_index=True, default='ABERTA', max_length=15)),
                ('custo_previsto', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('custo_real', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('observacoes', models.TextField(blank=True, null=True)),
                ('data_emissao', models.DateField(db_index=True, default=django.utils.timezone.now)),
                ('data_inicio', models.DateTimeField(blank=True, null=True)),
                ('data_finalizacao', models.DateTimeField(blank=True, null=True)),
                ('criado_em', models.DateTimeField(default=django.utils.timezone.now)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name='ordens_producao', to='cadastros.empresa')),
                ('ficha_tecnica', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ordens_producao', to='produto.fichatecnica')),
                ('produto_final', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ordens_producao', to='produto.produto')),
            ],
            options={
                'ordering': ['-data_emissao', '-id'],
            },
        ),
        migrations.CreateModel(
            name='OrdemProducaoItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('INSUMO', 'Insumo'), ('AVIAMENTO', 'Aviamento'), ('SERVICO', 'Serviço/Facção')], max_length=15)),
                ('descricao', models.CharField(blank=True, max_length=120, null=True)),
                ('quantidade_base', models.DecimalField(decimal_places=4, max_digits=12)),
                ('perda_percentual', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('quantidade_necessaria', models.DecimalField(decimal_places=4, max_digits=14)),
                ('custo_unitario_previsto', models.DecimalField(decimal_places=4, default=0, max_digits=12)),
                ('custo_unitario_real', models.DecimalField(decimal_places=4, default=0, max_digits=12)),
                ('custo_total_previsto', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('custo_total_real', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('observacoes', models.CharField(blank=True, max_length=200, null=True)),
                ('ordem_linha', models.PositiveIntegerField(default=1)),
                ('ficha_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='itens_ordem', to='produto.fichatecnicaitem')),
                ('fornecedor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='itens_ordem_producao', to='cadastros.fornecedor')),
                ('ordem', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='produto.ordemproducao')),
                ('produto', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='itens_ordem_producao', to='produto.produto')),
                ('unidade', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='produto.unidade')),
            ],
            options={
                'ordering': ['ordem_linha', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='ordemproducao',
            constraint=models.UniqueConstraint(fields=('empresa', 'numero'), name='uq_empresa_ordem_producao_numero'),
        ),
        migrations.AddIndex(
            model_name='ordemproducao',
            index=models.Index(fields=['empresa', 'status'], name='produto_ord_empresa_55be13_idx'),
        ),
        migrations.AddIndex(
            model_name='ordemproducao',
            index=models.Index(fields=['produto_final'], name='produto_ord_produto_3d6e20_idx'),
        ),
        migrations.AddIndex(
            model_name='ordemproducao',
            index=models.Index(fields=['ficha_tecnica'], name='produto_ord_ficha__4df699_idx'),
        ),
        migrations.AddIndex(
            model_name='ordemproducaoitem',
            index=models.Index(fields=['ordem', 'tipo'], name='produto_ord_ordem_i_10e52c_idx'),
        ),
        migrations.AddIndex(
            model_name='ordemproducaoitem',
            index=models.Index(fields=['produto'], name='produto_ord_produto_04d9ef_idx'),
        ),
    ]

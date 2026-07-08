from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('financeiro', '0011_movimentacaofinanceira_data_conciliacao_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='movimentacaofinanceira',
            name='origem',
            field=models.CharField(
                choices=[
                    ('MANUAL', 'Manual'),
                    ('PAGAR', 'Contas a pagar'),
                    ('RECEBER', 'Contas a receber'),
                    ('TRANSFERENCIA', 'Transferência entre caixas'),
                    ('CARTAO', 'Recebível de cartão'),
                    ('ANTECIPACAO', 'Antecipação de recebíveis'),
                ],
                db_index=True,
                default='MANUAL',
                max_length=15,
            ),
        ),
        migrations.AlterField(
            model_name='movimentacaofinanceira',
            name='status',
            field=models.CharField(
                choices=[
                    ('PREVISTA', 'Prevista'),
                    ('EFETIVA', 'Efetiva'),
                    ('CANCELADA', 'Cancelada'),
                    ('ANTECIPADA', 'Antecipada'),
                ],
                db_index=True,
                default='EFETIVA',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='receberitem',
            name='status',
            field=models.CharField(
                choices=[
                    ('PREVISTO', 'Previsto'),
                    ('EFETIVO', 'Efetivo'),
                    ('BAIXADO', 'Baixado'),
                    ('CANCELADO', 'Cancelado'),
                    ('ANTECIPADO', 'Antecipado'),
                ],
                default='PREVISTO',
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='AntecipacaoRecebivel',
            fields=[
                ('Idantecipacao', models.BigAutoField(primary_key=True, serialize=False)),
                ('documento', models.CharField(db_index=True, max_length=50)),
                ('data_antecipacao', models.DateField(db_index=True, default=django.utils.timezone.now)),
                ('taxa_percentual', models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ('valor_bruto', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('taxa_valor', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('valor_liquido', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('EFETIVA', 'Efetiva'), ('CANCELADA', 'Cancelada')], db_index=True, default='EFETIVA', max_length=10)),
                ('observacao', models.CharField(blank=True, default='', max_length=255)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('conta_bancaria', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='antecipacoes', to='financeiro.contabancaria')),
                ('criado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('empresa', models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='antecipacoes_recebiveis', to='cadastros.empresa')),
                ('idloja', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, to='cadastros.loja')),
            ],
            options={
                'db_table': 'financeiro_antecipacao_recebivel',
                'ordering': ['-data_antecipacao', '-Idantecipacao'],
            },
        ),
        migrations.CreateModel(
            name='AntecipacaoRecebivelItem',
            fields=[
                ('Idantecipacaoitem', models.BigAutoField(primary_key=True, serialize=False)),
                ('valor_bruto', models.DecimalField(decimal_places=2, max_digits=18)),
                ('taxa_valor', models.DecimalField(decimal_places=2, max_digits=18)),
                ('valor_liquido', models.DecimalField(decimal_places=2, max_digits=18)),
                ('antecipacao', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='itens', to='financeiro.antecipacaorecebivel')),
                ('movimentacao', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='antecipacao_item', to='financeiro.movimentacaofinanceira')),
                ('receber_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='antecipacoes', to='financeiro.receberitem')),
            ],
            options={
                'db_table': 'financeiro_antecipacao_recebivel_item',
            },
        ),
        migrations.AddIndex(
            model_name='antecipacaorecebivel',
            index=models.Index(fields=['empresa', 'data_antecipacao'], name='financeiro__empresa_1e920c_idx'),
        ),
        migrations.AddIndex(
            model_name='antecipacaorecebivel',
            index=models.Index(fields=['conta_bancaria', 'status'], name='financeiro__conta_b_5a06c7_idx'),
        ),
        migrations.AddIndex(
            model_name='antecipacaorecebivelitem',
            index=models.Index(fields=['antecipacao'], name='financeiro__antecipa_0906d5_idx'),
        ),
        migrations.AddIndex(
            model_name='antecipacaorecebivelitem',
            index=models.Index(fields=['receber_item'], name='financeiro__receber_9efdb0_idx'),
        ),
    ]

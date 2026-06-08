# Generated manually for Sysvar cashback support.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('financeiro', '0004_alter_caixa_options_caixa_tipo_caixa_and_more'),
        ('fiscal', '0004_vendapdvpagamento'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashbackConfig',
            fields=[
                ('Idcashbackconfig', models.BigAutoField(primary_key=True, serialize=False)),
                ('nome', models.CharField(default='Regra padrão', max_length=80)),
                ('ativo', models.BooleanField(default=False)),
                ('percentual', models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ('validade_dias', models.PositiveIntegerField(default=180)),
                ('valor_minimo_geracao', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('valor_minimo_uso', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('limite_uso_percentual', models.DecimalField(decimal_places=4, default=100, max_digits=7)),
                ('consumidor_final_participa', models.BooleanField(default=False)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'financeiro_cashback_config',
                'ordering': ['-ativo', 'Idcashbackconfig'],
            },
        ),
        migrations.CreateModel(
            name='CashbackMovimento',
            fields=[
                ('Idcashbackmovimento', models.BigAutoField(primary_key=True, serialize=False)),
                ('tipo', models.CharField(choices=[('CREDITO', 'Crédito'), ('DEBITO', 'Uso em venda'), ('ESTORNO', 'Estorno'), ('EXPIRACAO', 'Expiração')], db_index=True, max_length=10)),
                ('status', models.CharField(choices=[('ATIVO', 'Ativo'), ('CANCELADO', 'Cancelado')], db_index=True, default='ATIVO', max_length=10)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=18)),
                ('validade', models.DateField(blank=True, db_index=True, null=True)),
                ('observacao', models.CharField(blank=True, default='', max_length=255)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('cliente', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name='cashback_movimentos', to='cadastros.cliente')),
                ('criado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('venda_origem', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cashback_creditos', to='fiscal.vendapdv')),
                ('venda_uso', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cashback_usos', to='fiscal.vendapdv')),
            ],
            options={
                'db_table': 'financeiro_cashback_movimento',
                'ordering': ['-criado_em', '-Idcashbackmovimento'],
            },
        ),
        migrations.AddIndex(
            model_name='cashbackmovimento',
            index=models.Index(fields=['cliente', 'status'], name='ix_cashback_cliente_status'),
        ),
        migrations.AddIndex(
            model_name='cashbackmovimento',
            index=models.Index(fields=['tipo', 'status'], name='ix_cashback_tipo_status'),
        ),
    ]

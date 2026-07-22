from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def criar_tipos_padrao(apps, schema_editor):
    Empresa = apps.get_model('cadastros', 'Empresa')
    NatLancamento = apps.get_model('cadastros', 'Nat_Lancamento')
    TipoDespesaPdv = apps.get_model('financeiro', 'TipoDespesaPdv')

    padroes = [
        ('LAN', 'Lanche de loja', '3301', 'Despesas de loja', 'Lanche'),
        ('PAP', 'Papelaria', '3302', 'Despesas de loja', 'Papelaria'),
        ('SUP', 'Suprimentos de escritório', '3303', 'Despesas de loja', 'Suprimentos'),
        ('VALE', 'Vale operacional', '3304', 'Despesas de loja', 'Vales'),
        ('LIMP', 'Material de limpeza', '3305', 'Despesas de loja', 'Limpeza'),
    ]

    for empresa in Empresa.objects.all():
        for codigo_tipo, descricao, codigo_nat, categoria, subcategoria in padroes:
            natureza, _ = NatLancamento.objects.get_or_create(
                empresa=empresa,
                codigo=codigo_nat,
                defaults={
                    'categoria_principal': categoria,
                    'subcategoria': subcategoria,
                    'descricao': descricao,
                    'tipo': 'DESPESA',
                    'status': 'ATIVO',
                    'tipo_natureza': 'DEBITO',
                    'natureza_operacao': 'DESPESA',
                    'categoria_gerencial': 'Despesas administrativas',
                    'movimenta_financeiro': True,
                    'entra_dre': True,
                    'ativo': True,
                },
            )
            TipoDespesaPdv.objects.get_or_create(
                empresa=empresa,
                codigo=codigo_tipo,
                defaults={
                    'descricao': descricao,
                    'Idnatureza': natureza,
                    'ativo': True,
                    'exige_documento': False,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0017_funcionarios_salario'),
        ('financeiro', '0018_contabancaria_conta_contabil'),
    ]

    operations = [
        migrations.CreateModel(
            name='TipoDespesaPdv',
            fields=[
                ('Idtipodespesapdv', models.BigAutoField(primary_key=True, serialize=False)),
                ('codigo', models.CharField(max_length=20)),
                ('descricao', models.CharField(max_length=120)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('exige_documento', models.BooleanField(default=False)),
                ('data_cadastro', models.DateTimeField(default=django.utils.timezone.now)),
                ('Idnatureza', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tipos_despesa_pdv', to='cadastros.nat_lancamento')),
                ('empresa', models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='tipos_despesa_pdv', to='cadastros.empresa')),
            ],
            options={
                'db_table': 'financeiro_tipo_despesa_pdv',
                'ordering': ['descricao'],
            },
        ),
        migrations.AddConstraint(
            model_name='tipodespesapdv',
            constraint=models.UniqueConstraint(fields=('empresa', 'codigo'), name='uq_empresa_tipo_despesa_pdv_codigo'),
        ),
        migrations.RunPython(criar_tipos_padrao, migrations.RunPython.noop),
    ]

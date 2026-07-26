from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0019_tipodespesapdv'),
    ]

    operations = [
        migrations.AddField(
            model_name='formapagamento',
            name='tef_habilitado',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='formapagamento',
            name='tef_modalidade',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='formapagamento',
            name='tef_adquirente_codigo',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='formapagamento',
            name='tef_terminal_logico',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]

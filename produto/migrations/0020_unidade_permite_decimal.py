from django.db import migrations, models


def backfill_unidades_decimais(apps, schema_editor):
    Unidade = apps.get_model('produto', 'Unidade')
    codigos_decimais = {'M', 'MT', 'M2', 'M3', 'KG', 'G', 'L', 'LT', 'ML'}
    termos_decimais = ('metro', 'quilo', 'kg', 'litro', 'grama')

    for unidade in Unidade.objects.all():
        codigo = (unidade.Codigo or '').strip().upper()
        descricao = (unidade.Descricao or '').strip().lower()
        permite = codigo in codigos_decimais or any(term in descricao for term in termos_decimais)
        if permite:
            unidade.permite_decimal = True
            unidade.save(update_fields=['permite_decimal'])


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0019_alter_ordemproducao_data_emissao'),
    ]

    operations = [
        migrations.AddField(
            model_name='unidade',
            name='permite_decimal',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_unidades_decimais, migrations.RunPython.noop),
    ]

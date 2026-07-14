from django.db import migrations, models


def marcar_matrizes(apps, schema_editor):
    Loja = apps.get_model('cadastros', 'Loja')
    Loja.objects.filter(Matriz='SIM').update(tipo_unidade='MATRIZ')


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0012_alter_fornecedor_categoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='loja',
            name='tipo_unidade',
            field=models.CharField(
                choices=[
                    ('LOJA', 'Loja'),
                    ('MATRIZ', 'Matriz / Estoque central'),
                    ('FABRICA', 'Fábrica / Produção'),
                ],
                db_index=True,
                default='LOJA',
                max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name='loja',
            index=models.Index(fields=['tipo_unidade'], name='cadastros_l_tipo_u_9df230_idx'),
        ),
        migrations.RunPython(marcar_matrizes, migrations.RunPython.noop),
    ]

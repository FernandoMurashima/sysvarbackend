from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0023_ordemproducaograde'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='status_faccao',
            field=models.CharField(
                choices=[
                    ('PENDENTE', 'Pendente'),
                    ('ENVIADO', 'Enviado'),
                    ('RETORNADO', 'Retornado'),
                ],
                db_index=True,
                default='PENDENTE',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='documento_faccao',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='data_envio_faccao',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='data_retorno_faccao',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='quantidade_enviada_faccao',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='ordemproducaoitem',
            name='quantidade_retornada_faccao',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AddIndex(
            model_name='ordemproducaoitem',
            index=models.Index(fields=['status_faccao'], name='produto_ord_status__5a4c1d_idx'),
        ),
    ]

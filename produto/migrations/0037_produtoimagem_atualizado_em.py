from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0036_estoquemovimentacao_origem_recebimento'),
    ]

    operations = [
        migrations.AddField(
            model_name='produtoimagem',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]

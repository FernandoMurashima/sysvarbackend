import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produto', '0018_ordemproducao_ordemproducaoitem'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ordemproducao',
            name='data_emissao',
            field=models.DateField(db_index=True, default=django.utils.timezone.localdate),
        ),
    ]

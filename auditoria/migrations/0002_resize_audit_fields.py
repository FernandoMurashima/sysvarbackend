from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('auditoria', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(max_length=32, db_index=True),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='app_label',
            field=models.CharField(max_length=50, db_index=True),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='model',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='object_id',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='ip',
            field=models.CharField(blank=True, max_length=45, null=True),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='user_agent',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]

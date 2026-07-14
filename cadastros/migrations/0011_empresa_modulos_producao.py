from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0010_alter_planocontabil_classe"),
    ]

    operations = [
        migrations.AddField(
            model_name="empresa",
            name="usa_producao",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="empresa",
            name="usa_ficha_tecnica",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="empresa",
            name="usa_faccao",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="empresa",
            name="usa_distribuicao_producao",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddIndex(
            model_name="empresa",
            index=models.Index(fields=["usa_producao"], name="cadastros_e_usa_pro_9d9f8d_idx"),
        ),
    ]

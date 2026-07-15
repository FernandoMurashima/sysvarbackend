from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0026_popula_ncms_insumo_uso_consumo"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventarioestoqueitem",
            name="contado",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AlterField(
            model_name="inventarioestoque",
            name="status",
            field=models.CharField(choices=[("ABERTO", "Aberto"), ("VALIDADO", "Validado"), ("FECHADO", "Fechado"), ("CANCELADO", "Cancelado")], db_index=True, default="ABERTO", max_length=10),
        ),
    ]

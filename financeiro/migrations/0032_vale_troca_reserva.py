import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0008_hubcomandoadministrativo"),
        ("financeiro", "0031_receberitem_adquirente_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ValeTrocaReserva",
            fields=[
                ("Idvaletrocareserva", models.BigAutoField(primary_key=True, serialize=False)),
                ("venda_uuid", models.UUIDField(db_index=True)),
                ("operacao_uuid", models.UUIDField(db_index=True)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=18)),
                ("status", models.CharField(choices=[("RESERVADA", "Reservada"), ("CONSUMIDA", "Consumida"), ("CANCELADA", "Cancelada")], db_index=True, default="RESERVADA", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="vales_troca_reservas", to="cadastros.empresa")),
                ("hub", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="vales_troca_reservas", to="hub.sysvarhub")),
                ("vale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reservas", to="financeiro.valetroca")),
            ],
            options={
                "db_table": "financeiro_vale_troca_reserva",
                "ordering": ["-criado_em", "-Idvaletrocareserva"],
            },
        ),
        migrations.AddIndex(
            model_name="valetrocareserva",
            index=models.Index(fields=["empresa", "status"], name="ix_vale_res_emp_status"),
        ),
        migrations.AddIndex(
            model_name="valetrocareserva",
            index=models.Index(fields=["vale", "status"], name="ix_vale_res_vale_status"),
        ),
        migrations.AddIndex(
            model_name="valetrocareserva",
            index=models.Index(fields=["hub", "venda_uuid"], name="ix_vale_res_hub_venda"),
        ),
        migrations.AddConstraint(
            model_name="valetrocareserva",
            constraint=models.UniqueConstraint(fields=("hub", "venda_uuid", "operacao_uuid"), name="uq_vale_reserva_operacao_hub"),
        ),
    ]

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0010_hubdevolucaofaixanumeracao"),
        ("financeiro", "0035_forma_pagamento_condicoes"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashbackReserva",
            fields=[
                ("Idcashbackreserva", models.BigAutoField(primary_key=True, serialize=False)),
                ("venda_uuid", models.UUIDField(db_index=True)),
                ("operacao_uuid", models.UUIDField(db_index=True)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=18)),
                ("status", models.CharField(choices=[("RESERVADA", "Reservada"), ("CONSUMIDA", "Consumida"), ("CANCELADA", "Cancelada")], db_index=True, default="RESERVADA", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("cliente", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashback_reservas", to="cadastros.cliente")),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashback_reservas", to="cadastros.empresa")),
                ("hub", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashback_reservas", to="hub.sysvarhub")),
            ],
            options={
                "db_table": "financeiro_cashback_reserva",
                "ordering": ["-criado_em", "-Idcashbackreserva"],
            },
        ),
        migrations.AddIndex(
            model_name="cashbackreserva",
            index=models.Index(fields=["empresa", "cliente", "status"], name="ix_cb_res_emp_cli_status"),
        ),
        migrations.AddIndex(
            model_name="cashbackreserva",
            index=models.Index(fields=["hub", "venda_uuid"], name="ix_cb_res_hub_venda"),
        ),
        migrations.AddConstraint(
            model_name="cashbackreserva",
            constraint=models.UniqueConstraint(fields=("hub", "venda_uuid", "operacao_uuid"), name="uq_cashback_reserva_operacao_hub"),
        ),
    ]

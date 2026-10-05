from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0009_hubvendafaixanumeracao"),
    ]

    operations = [
        migrations.CreateModel(
            name="HubDevolucaoFaixaNumeracao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("inicio", models.PositiveIntegerField()),
                ("fim", models.PositiveIntegerField()),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("hub", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="faixas_numeracao_devolucao", to="hub.sysvarhub")),
            ],
            options={
                "ordering": ["hub_id", "inicio"],
                "indexes": [models.Index(fields=["hub", "inicio", "fim"], name="ix_hub_dev_faixa_hub_int")],
                "constraints": [
                    models.UniqueConstraint(fields=("hub", "inicio", "fim"), name="uq_hub_dev_faixa_intervalo"),
                    models.CheckConstraint(check=models.Q(inicio__lte=models.F("fim")), name="ck_hub_dev_faixa_ordem"),
                ],
            },
        ),
    ]

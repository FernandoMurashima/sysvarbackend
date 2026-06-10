# Generated manually for Sysvar exchange credit support.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("fiscal", "0005_vendadevolucao_vendadevolucaoitem_nfe_devolucao"),
        ("financeiro", "0005_cashbackconfig_cashbackmovimento"),
    ]

    operations = [
        migrations.CreateModel(
            name="ValeTroca",
            fields=[
                ("Idvaletroca", models.BigAutoField(primary_key=True, serialize=False)),
                ("documento", models.CharField(db_index=True, max_length=50, unique=True)),
                ("valor_original", models.DecimalField(decimal_places=2, max_digits=18)),
                ("saldo", models.DecimalField(decimal_places=2, max_digits=18)),
                ("status", models.CharField(choices=[("ABERTO", "Aberto"), ("USADO", "Usado"), ("CANCELADO", "Cancelado"), ("EXPIRADO", "Expirado")], db_index=True, default="ABERTO", max_length=12)),
                ("validade", models.DateField(blank=True, db_index=True, null=True)),
                ("observacao", models.CharField(blank=True, default="", max_length=255)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("cliente", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="vales_troca", to="cadastros.cliente")),
                ("criado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("devolucao", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="vale_troca", to="fiscal.vendadevolucao")),
                ("loja", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="vales_troca", to="cadastros.loja")),
            ],
            options={
                "db_table": "financeiro_vale_troca",
                "ordering": ["-criado_em", "-Idvaletroca"],
            },
        ),
        migrations.CreateModel(
            name="ValeTrocaMovimento",
            fields=[
                ("Idvaletrocamov", models.BigAutoField(primary_key=True, serialize=False)),
                ("tipo", models.CharField(choices=[("CREDITO", "Crédito gerado"), ("USO", "Uso em venda"), ("ESTORNO", "Estorno")], db_index=True, max_length=10)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=18)),
                ("saldo_apos", models.DecimalField(decimal_places=2, max_digits=18)),
                ("observacao", models.CharField(blank=True, default="", max_length=255)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("criado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("vale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movimentos", to="financeiro.valetroca")),
                ("venda_uso", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vales_troca_usados", to="fiscal.vendapdv")),
            ],
            options={
                "db_table": "financeiro_vale_troca_movimento",
                "ordering": ["-criado_em", "-Idvaletrocamov"],
            },
        ),
        migrations.AddIndex(
            model_name="valetroca",
            index=models.Index(fields=["cliente", "status"], name="ix_vale_troca_cliente_status"),
        ),
        migrations.AddIndex(
            model_name="valetroca",
            index=models.Index(fields=["loja", "status"], name="ix_vale_troca_loja_status"),
        ),
        migrations.AddIndex(
            model_name="valetrocamovimento",
            index=models.Index(fields=["vale", "tipo"], name="ix_vale_troca_mov_vale_tipo"),
        ),
        migrations.AddIndex(
            model_name="valetrocamovimento",
            index=models.Index(fields=["venda_uso"], name="ix_vale_troca_mov_venda"),
        ),
    ]

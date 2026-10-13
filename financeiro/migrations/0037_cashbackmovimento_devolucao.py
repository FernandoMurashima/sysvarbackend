from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0036_cashback_reserva"),
        ("fiscal", "0043_vendadevolucao_documento_empresa_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashbackmovimento",
            name="devolucao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cashback_estornos",
                to="fiscal.vendadevolucao",
            ),
        ),
        migrations.AddIndex(
            model_name="cashbackmovimento",
            index=models.Index(fields=["devolucao", "tipo"], name="ix_cashback_dev_tipo"),
        ),
    ]

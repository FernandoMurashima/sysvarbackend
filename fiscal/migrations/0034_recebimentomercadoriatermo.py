from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cadastros", "0029_centrocusto"),
        ("fiscal", "0033_xmlfornecedorrecebido_quantidade_faturada"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecebimentoMercadoriaTermo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("encerrado_em", models.DateTimeField()),
                ("observacao_divergencia", models.TextField(blank=True, default="")),
                ("possui_divergencia", models.BooleanField(db_index=True, default=False)),
                ("snapshot", models.JSONField(default=dict)),
                ("hash_sha256", models.CharField(max_length=64)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_mercadoria_termos", to="cadastros.empresa")),
                ("encerrado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_mercadoria_encerrados", to=settings.AUTH_USER_MODEL)),
                ("recebimento", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="termo_encerramento", to="fiscal.recebimentomercadoriaestoque")),
            ],
            options={
                "db_table": "fiscal_recebimento_mercadoria_termo",
                "ordering": ["-encerrado_em", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="recebimentomercadoriatermo",
            index=models.Index(fields=["empresa", "encerrado_em"], name="ix_receb_term_emp_enc"),
        ),
        migrations.AddIndex(
            model_name="recebimentomercadoriatermo",
            index=models.Index(fields=["empresa", "possui_divergencia"], name="ix_receb_term_emp_div"),
        ),
    ]

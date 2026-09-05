from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cadastros", "0029_centrocusto"),
        ("fiscal", "0034_recebimentomercadoriatermo"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecebimentoMercadoriaEfetivacaoEstoque",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("efetivado_em", models.DateTimeField()),
                ("quantidade_total", models.DecimalField(decimal_places=3, max_digits=14)),
                ("quantidade_skus", models.PositiveIntegerField()),
                ("hash_termo", models.CharField(max_length=64)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("efetivado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_mercadoria_estoque_efetivados", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_mercadoria_efetivacoes", to="cadastros.empresa")),
                ("loja", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_mercadoria_efetivacoes", to="cadastros.loja")),
                ("recebimento", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="efetivacao_estoque", to="fiscal.recebimentomercadoriaestoque")),
                ("termo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="efetivacoes_estoque", to="fiscal.recebimentomercadoriatermo")),
            ],
            options={
                "db_table": "fiscal_recebimento_mercadoria_efetivacao_estoque",
                "ordering": ["-efetivado_em", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="recebimentomercadoriaefetivacaoestoque",
            index=models.Index(fields=["empresa", "loja"], name="ix_receb_efet_emp_loja"),
        ),
        migrations.AddIndex(
            model_name="recebimentomercadoriaefetivacaoestoque",
            index=models.Index(fields=["empresa", "efetivado_em"], name="ix_receb_efet_emp_data"),
        ),
    ]

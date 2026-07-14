from django.db import migrations, models
import django.db.models.deletion


def criar_grade_legacy(apps, schema_editor):
    OrdemProducao = apps.get_model("produto", "OrdemProducao")
    OrdemProducaoGrade = apps.get_model("produto", "OrdemProducaoGrade")
    for ordem in OrdemProducao.objects.exclude(sku_final_id__isnull=True):
        if not OrdemProducaoGrade.objects.filter(ordem_id=ordem.pk).exists():
            OrdemProducaoGrade.objects.create(
                ordem_id=ordem.pk,
                sku_final_id=ordem.sku_final_id,
                quantidade=ordem.quantidade or 0,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0022_ordemproducao_sku_final"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrdemProducaoGrade",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantidade", models.DecimalField(decimal_places=3, max_digits=12)),
                (
                    "ordem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grade_producao",
                        to="produto.ordemproducao",
                    ),
                ),
                (
                    "sku_final",
                    models.ForeignKey(
                        help_text="SKU acabado produzido nesta linha da OP.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="grades_ordem_producao",
                        to="produto.produtodetalhe",
                    ),
                ),
            ],
            options={
                "ordering": ["sku_final__idcor__Descricao", "sku_final__idtamanho__Tamanho", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="ordemproducaograde",
            constraint=models.UniqueConstraint(fields=("ordem", "sku_final"), name="uq_op_grade_sku"),
        ),
        migrations.AddIndex(
            model_name="ordemproducaograde",
            index=models.Index(fields=["ordem"], name="produto_ord_ordem_i_4ca92c_idx"),
        ),
        migrations.AddIndex(
            model_name="ordemproducaograde",
            index=models.Index(fields=["sku_final"], name="produto_ord_sku_fin_4ef8d7_idx"),
        ),
        migrations.RunPython(criar_grade_legacy, migrations.RunPython.noop),
    ]

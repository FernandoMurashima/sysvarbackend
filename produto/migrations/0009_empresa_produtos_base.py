from django.db import migrations, models
import django.db.models.deletion


def vincular_empresa_produtos(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Colecao = apps.get_model("produto", "Colecao")
    Grupo = apps.get_model("produto", "Grupo")
    Subgrupo = apps.get_model("produto", "Subgrupo")
    Tabelapreco = apps.get_model("produto", "Tabelapreco")
    Produto = apps.get_model("produto", "Produto")
    Promocao = apps.get_model("produto", "Promocao")
    Pack = apps.get_model("produto", "Pack")

    empresa_padrao = Empresa.objects.order_by("id").first()
    if not empresa_padrao:
        empresa_padrao = Empresa.objects.create(nome="CISVAR Base Atual", nome_fantasia="CISVAR Base Atual")

    Colecao.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    Grupo.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    Tabelapreco.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    Produto.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    Pack.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)

    for subgrupo in Subgrupo.objects.select_related("Idgrupo").filter(empresa__isnull=True):
        subgrupo.empresa = getattr(subgrupo.Idgrupo, "empresa", None) or empresa_padrao
        subgrupo.save(update_fields=["empresa"])

    for promocao in Promocao.objects.filter(empresa__isnull=True).prefetch_related("lojas"):
        loja = promocao.lojas.first()
        promocao.empresa = getattr(loja, "empresa", None) or empresa_padrao
        promocao.save(update_fields=["empresa"])


def desvincular_empresa_produtos(apps, schema_editor):
    for model_name in ("Colecao", "Grupo", "Subgrupo", "Tabelapreco", "Produto", "Promocao", "Pack"):
        Model = apps.get_model("produto", model_name)
        Model.objects.update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0006_cliente_fornecedor_funcionario_empresa"),
        ("produto", "0008_promocao"),
    ]

    operations = [
        migrations.AddField(
            model_name="colecao",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="colecoes",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="grupo",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="grupos_produto",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="subgrupo",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subgrupos_produto",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="tabelapreco",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tabelas_preco",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="produto",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="produtos",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="promocao",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promocoes_produto",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="pack",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="packs_produto",
                to="cadastros.empresa",
            ),
        ),
        migrations.RunPython(vincular_empresa_produtos, desvincular_empresa_produtos),
    ]

from django.db import migrations, models


def classificar_naturezas(apps, schema_editor):
    NatLancamento = apps.get_model("cadastros", "Nat_Lancamento")
    for natureza in NatLancamento.objects.all():
        tipo_natureza = (natureza.tipo_natureza or "").upper()
        tipo = (natureza.tipo or "").upper()
        descricao = (natureza.descricao or "").upper()

        if "TRANSF" in tipo or "TRANSF" in descricao:
            natureza.natureza_operacao = "TRANSFERENCIA"
            natureza.entra_dre = False
        elif tipo_natureza == "CREDITO":
            natureza.natureza_operacao = "RECEITA"
        elif tipo_natureza == "DEBITO":
            natureza.natureza_operacao = "DESPESA"
        else:
            natureza.natureza_operacao = "AJUSTE"

        natureza.categoria_gerencial = natureza.categoria_principal or ""
        natureza.ativo = str(natureza.status or "").upper() not in {"INATIVO", "BLOQUEADO", "CANCELADO"}
        natureza.save(update_fields=["natureza_operacao", "categoria_gerencial", "entra_dre", "ativo"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0007_rename_cadastros_e_nome_c3e4c1_idx_cadastros_e_nome_1ec0bf_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="nat_lancamento",
            name="ativo",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="nat_lancamento",
            name="categoria_gerencial",
            field=models.CharField(blank=True, db_index=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="nat_lancamento",
            name="conta_contabil",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="nat_lancamento",
            name="entra_dre",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="nat_lancamento",
            name="movimenta_financeiro",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="nat_lancamento",
            name="natureza_operacao",
            field=models.CharField(db_index=True, default="DESPESA", max_length=20),
        ),
        migrations.RunPython(classificar_naturezas, migrations.RunPython.noop),
    ]

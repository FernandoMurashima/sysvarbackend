from django.db import migrations, models
import django.db.models.deletion


def preencher_loja_nfe_devolucao(apps, schema_editor):
    NFeDevolucao = apps.get_model("fiscal", "NFeDevolucao")
    for nfe in NFeDevolucao.objects.select_related("devolucao").filter(loja__isnull=True):
        if nfe.devolucao_id and nfe.devolucao.loja_id:
            nfe.loja_id = nfe.devolucao.loja_id
            nfe.save(update_fields=["loja"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0027_cargos_funcionarios_basicos"),
        ("fiscal", "0040_nfce_hub_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="nfedevolucao",
            name="loja",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="nfes_devolucao",
                to="cadastros.loja",
            ),
        ),
        migrations.AlterField(
            model_name="nfedevolucao",
            name="status",
            field=models.CharField(
                choices=[
                    ("DIGITADA", "Digitada"),
                    ("PENDENTE_TRANSMISSAO", "Pendente transmissao"),
                    ("EMITINDO", "Emitindo"),
                    ("AUTORIZADA", "Autorizada"),
                    ("REJEITADA", "Rejeitada"),
                    ("ERRO_GERACAO", "Erro geracao"),
                    ("CANCELADA", "Cancelada"),
                ],
                db_index=True,
                default="DIGITADA",
                max_length=24,
            ),
        ),
        migrations.RunPython(preencher_loja_nfe_devolucao, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="nfedevolucao",
            name="uq_nfe_devolucao_serie_numero",
        ),
        migrations.AlterField(
            model_name="nfedevolucao",
            name="loja",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="nfes_devolucao",
                to="cadastros.loja",
            ),
        ),
        migrations.AddIndex(
            model_name="nfedevolucao",
            index=models.Index(fields=["loja", "status"], name="ix_nfe_dev_loja_status"),
        ),
        migrations.AddConstraint(
            model_name="nfedevolucao",
            constraint=models.UniqueConstraint(
                fields=("loja", "ambiente", "modelo", "serie", "numero"),
                name="uq_nfe_dev_loja_amb_mod_serie_num",
            ),
        ),
    ]

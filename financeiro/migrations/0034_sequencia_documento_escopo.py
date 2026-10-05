from django.db import migrations, models


def preencher_escopo_empresa(apps, schema_editor):
    SequenciaDocumento = apps.get_model("financeiro", "SequenciaDocumento")
    SequenciaDocumento.objects.filter(escopo="").update(escopo="EMPRESA")
    SequenciaDocumento.objects.filter(escopo__isnull=True).update(escopo="EMPRESA")


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0033_sequencia_documento_vale_troca"),
    ]

    operations = [
        migrations.AddField(
            model_name="sequenciadocumento",
            name="escopo",
            field=models.CharField(default="EMPRESA", max_length=40),
        ),
        migrations.RunPython(preencher_escopo_empresa, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="sequenciadocumento",
            name="uq_seq_doc_empresa_tipo",
        ),
        migrations.AlterField(
            model_name="sequenciadocumento",
            name="tipo_documento",
            field=models.CharField(
                choices=[
                    ("VALE_TROCA", "Vale-Troca"),
                    ("VENDA", "Venda"),
                    ("DEVOLUCAO", "Devolucao"),
                    ("DISTRIBUICAO", "Distribuicao"),
                    ("PEDIDO_VENDA_DISTRIBUICAO", "Pedido de Venda da Distribuicao"),
                ],
                max_length=40,
            ),
        ),
        migrations.AddConstraint(
            model_name="sequenciadocumento",
            constraint=models.UniqueConstraint(
                fields=("empresa", "tipo_documento", "escopo"),
                name="uq_seq_doc_empresa_tipo_escopo",
            ),
        ),
    ]

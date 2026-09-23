from django.db import migrations, models


def convergir_tipos_credito(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamento.objects.filter(tipo__in=["CREDITO_ROTATIVO", "CREDITO_PARCELADO"]).update(tipo="CREDITO")


def reverter_tipos_credito(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamento.objects.filter(tipo="CREDITO").update(tipo="CREDITO_ROTATIVO")


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0026_nfe_xml_efetivacao"),
    ]

    operations = [
        migrations.AddField(
            model_name="prazopagamento",
            name="finalidade",
            field=models.CharField(
                choices=[("PAGAR", "Pagar"), ("RECEBER", "Receber"), ("AMBOS", "Ambos")],
                default="AMBOS",
                max_length=8,
            ),
        ),
        migrations.RunPython(convergir_tipos_credito, reverter_tipos_credito),
        migrations.AlterField(
            model_name="formapagamento",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("DINHEIRO", "Dinheiro"),
                    ("PIX", "Pix"),
                    ("DEBITO", "Cartão de débito"),
                    ("CREDITO", "Cartão de crédito"),
                    ("BOLETO", "Boleto"),
                    ("TRANSFERENCIA", "Transferência"),
                    ("OUTRO", "Outro"),
                ],
                default="OUTRO",
                max_length=24,
            ),
        ),
    ]

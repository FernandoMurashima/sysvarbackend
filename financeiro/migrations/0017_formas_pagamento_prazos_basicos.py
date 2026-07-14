from django.db import migrations


def criar_formas(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamentoParcela = apps.get_model("financeiro", "FormaPagamentoParcela")

    formas = [
        ("15D", "15 dias - 1 parcela", 1, [(1, 15, "100.000000")]),
        ("15_30", "15 e 30 dias - 2 parcelas iguais", 2, [(1, 15, "50.000000"), (2, 30, "50.000000")]),
        ("15_30_45", "15, 30 e 45 dias - 3 parcelas iguais", 3, [(1, 15, "33.333333"), (2, 30, "33.333333"), (3, 45, "33.333334")]),
    ]

    for empresa in Empresa.objects.all():
        for codigo, descricao, parcelas, detalhes in formas:
            forma, _ = FormaPagamento.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "num_parcelas": parcelas,
                    "ativo": True,
                    "gera_recebivel_bancario": False,
                    "prazo_credito_dias": 0,
                    "taxa_percentual": 0,
                    "taxa_fixa": 0,
                },
            )
            for ordem, dias, percentual in detalhes:
                FormaPagamentoParcela.objects.update_or_create(
                    forma=forma,
                    ordem=ordem,
                    defaults={
                        "dias": dias,
                        "percentual": percentual,
                        "valor_fixo": None,
                    },
                )


def remover_formas(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamento.objects.filter(codigo__in=["15D", "15_30", "15_30_45"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0016_configfinanceira_and_more"),
    ]

    operations = [
        migrations.RunPython(criar_formas, remover_formas),
    ]

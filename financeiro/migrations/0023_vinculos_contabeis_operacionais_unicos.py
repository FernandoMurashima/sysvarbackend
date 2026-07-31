from django.db import migrations


def ajustar_vinculos_contabeis(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    PlanoContabil = apps.get_model("cadastros", "PlanoContabil")
    Caixa = apps.get_model("financeiro", "Caixa")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")

    def conta(empresa, codigo, descricao):
        obj, _ = PlanoContabil.objects.update_or_create(
            empresa=empresa,
            codigo=codigo,
            defaults={
                "descricao": descricao[:160],
                "classe": "ATIVO",
                "natureza": "DEBITO",
                "nivel": codigo.count(".") + 1,
                "analitica": True,
                "ativa": True,
            },
        )
        return obj

    for empresa in Empresa.objects.all():
        contas_caixa = {
            "MATRIZ": conta(empresa, "1.1.01.001", "Caixa Loja Matriz"),
            "LOJA": conta(empresa, "1.1.01.002", "Caixa Loja Filial"),
            "FABRICA": conta(empresa, "1.1.01.003", "Caixa Fabrica"),
            "MASTER": conta(empresa, "1.1.01.004", "Caixa Master"),
        }

        for caixa in Caixa.objects.filter(empresa=empresa).select_related("idloja"):
            texto = f"{caixa.descricao or ''} {getattr(caixa.idloja, 'nome_loja', '') or ''}".lower()
            if "fabrica" in texto or "fábrica" in texto:
                plano = contas_caixa["FABRICA"]
            elif caixa.tipo_caixa == "MASTER":
                plano = contas_caixa["MASTER"]
            else:
                tipo_unidade = getattr(caixa.idloja, "tipo_unidade", "") or "LOJA"
                plano = contas_caixa.get(tipo_unidade, contas_caixa["LOJA"])
            if caixa.conta_contabil != plano.codigo:
                caixa.conta_contabil = plano.codigo
                caixa.save(update_fields=["conta_contabil"])

        for index, conta_bancaria in enumerate(ContaBancaria.objects.filter(empresa=empresa).order_by("Idconta"), start=1):
            codigo = f"1.1.02.{index:03d}"
            descricao = f"Conta Corrente {conta_bancaria.descricao}"
            plano = conta(empresa, codigo, descricao)
            if conta_bancaria.conta_contabil != plano.codigo:
                conta_bancaria.conta_contabil = plano.codigo
                conta_bancaria.save(update_fields=["conta_contabil"])


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0022_caixa_conta_contabil_plano_operacional"),
    ]

    operations = [
        migrations.RunPython(ajustar_vinculos_contabeis, migrations.RunPython.noop),
    ]

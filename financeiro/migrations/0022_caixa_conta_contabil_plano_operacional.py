from django.db import migrations, models


def criar_contas_operacionais(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    PlanoContabil = apps.get_model("cadastros", "PlanoContabil")
    Caixa = apps.get_model("financeiro", "Caixa")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")

    def conta(empresa, codigo, descricao):
        obj, _ = PlanoContabil.objects.update_or_create(
            empresa=empresa,
            codigo=codigo,
            defaults={
                "descricao": descricao,
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

        caixas = Caixa.objects.filter(empresa=empresa).select_related("idloja").order_by("Idcaixa")
        for caixa in caixas:
            if caixa.conta_contabil:
                continue
            if caixa.tipo_caixa == "MASTER":
                plano = contas_caixa["MASTER"]
            else:
                tipo_unidade = getattr(caixa.idloja, "tipo_unidade", "") or "LOJA"
                plano = contas_caixa.get(tipo_unidade, contas_caixa["LOJA"])
            caixa.conta_contabil = plano.codigo
            caixa.save(update_fields=["conta_contabil"])

        contas_bancarias = ContaBancaria.objects.filter(empresa=empresa).order_by("Idconta")
        for index, conta_bancaria in enumerate(contas_bancarias, start=1):
            if conta_bancaria.conta_contabil:
                continue
            codigo = f"1.1.02.{index:03d}"
            descricao = f"Conta Corrente {conta_bancaria.descricao}"
            plano = conta(empresa, codigo, descricao[:160])
            conta_bancaria.conta_contabil = plano.codigo
            conta_bancaria.save(update_fields=["conta_contabil"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0017_funcionarios_salario"),
        ("financeiro", "0021_rename_financeiro__empresa_1e920c_idx_financeiro__empresa_c8fd7f_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="caixa",
            name="conta_contabil",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.RunPython(criar_contas_operacionais, migrations.RunPython.noop),
    ]

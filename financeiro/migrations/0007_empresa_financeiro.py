from django.db import migrations, models
import django.db.models.deletion


def _primeira_empresa(apps):
    Empresa = apps.get_model("cadastros", "Empresa")
    return Empresa.objects.order_by("id").first()


def preencher_empresas(apps, schema_editor):
    empresa_padrao = _primeira_empresa(apps)
    if not empresa_padrao:
        return

    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    CashbackConfig = apps.get_model("financeiro", "CashbackConfig")
    CashbackMovimento = apps.get_model("financeiro", "CashbackMovimento")
    ValeTroca = apps.get_model("financeiro", "ValeTroca")
    Caixa = apps.get_model("financeiro", "Caixa")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")
    MovimentacaoFinanceira = apps.get_model("financeiro", "MovimentacaoFinanceira")
    Pagar = apps.get_model("financeiro", "Pagar")
    Receber = apps.get_model("financeiro", "Receber")

    FormaPagamento.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)
    CashbackConfig.objects.filter(empresa__isnull=True).update(empresa=empresa_padrao)

    for caixa in Caixa.objects.filter(empresa__isnull=True).select_related("idloja"):
        caixa.empresa_id = getattr(caixa.idloja, "empresa_id", None) or empresa_padrao.id
        caixa.save(update_fields=["empresa"])

    for conta in ContaBancaria.objects.filter(empresa__isnull=True).select_related("idloja"):
        conta.empresa_id = getattr(conta.idloja, "empresa_id", None) or empresa_padrao.id
        conta.save(update_fields=["empresa"])

    for mov in MovimentacaoFinanceira.objects.filter(empresa__isnull=True).select_related("idloja"):
        mov.empresa_id = getattr(mov.idloja, "empresa_id", None) or empresa_padrao.id
        mov.save(update_fields=["empresa"])

    for titulo in Pagar.objects.filter(empresa__isnull=True).select_related("idloja", "idfornecedor"):
        titulo.empresa_id = (
            getattr(titulo.idloja, "empresa_id", None)
            or getattr(titulo.idfornecedor, "empresa_id", None)
            or empresa_padrao.id
        )
        titulo.save(update_fields=["empresa"])

    for titulo in Receber.objects.filter(empresa__isnull=True).select_related("idloja", "idcliente"):
        titulo.empresa_id = (
            getattr(titulo.idloja, "empresa_id", None)
            or getattr(titulo.idcliente, "empresa_id", None)
            or empresa_padrao.id
        )
        titulo.save(update_fields=["empresa"])

    for mov in CashbackMovimento.objects.filter(empresa__isnull=True).select_related("cliente", "venda_origem", "venda_uso"):
        mov.empresa_id = (
            getattr(mov.venda_origem, "empresa_id", None)
            or getattr(mov.venda_uso, "empresa_id", None)
            or getattr(mov.cliente, "empresa_id", None)
            or empresa_padrao.id
        )
        mov.save(update_fields=["empresa"])

    for vale in ValeTroca.objects.filter(empresa__isnull=True).select_related("loja", "cliente"):
        vale.empresa_id = (
            getattr(vale.loja, "empresa_id", None)
            or getattr(vale.cliente, "empresa_id", None)
            or empresa_padrao.id
        )
        vale.save(update_fields=["empresa"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0006_cliente_fornecedor_funcionario_empresa"),
        ("fiscal", "0005_vendadevolucao_vendadevolucaoitem_nfe_devolucao"),
        ("financeiro", "0006_valetroca_valetrocamovimento"),
    ]

    operations = [
        migrations.AddField(
            model_name="formapagamento",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="formas_pagamento", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="cashbackconfig",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashback_configs", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="cashbackmovimento",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashback_movimentos", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="valetroca",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="vales_troca", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="caixa",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="caixas", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="contas_bancarias", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="movimentacaofinanceira",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="movimentacoes_financeiras", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="pagar",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="titulos_pagar", to="cadastros.empresa"),
        ),
        migrations.AddField(
            model_name="receber",
            name="empresa",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="titulos_receber", to="cadastros.empresa"),
        ),
        migrations.RunPython(preencher_empresas, migrations.RunPython.noop),
    ]

from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def popular_regras_lucro_real_teste(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Loja = apps.get_model("cadastros", "Loja")
    Cfop = apps.get_model("fiscal", "Cfop")
    RegraTributaria = apps.get_model("fiscal", "RegraTributaria")
    Tributo = apps.get_model("fiscal", "Tributo")

    Empresa.objects.update(regime_tributario="LUCRO_REAL")
    Loja.objects.update(regime_tributario="LUCRO_REAL")

    inicio = timezone.localdate()
    tributos_config = {
        "ICMS": Decimal("18.0000"),
        "PIS": Decimal("1.6500"),
        "COFINS": Decimal("7.6000"),
    }

    operacoes = [
        {
            "prefixo": "Compra matéria-prima",
            "tipo_operacao": "COMPRA",
            "tipo_produto": "INSUMO",
            "cfops": ["1101", "2101"],
            "permite_credito": True,
            "compoe_custo": False,
            "entra_dre": False,
            "observacoes": "Regra base de teste para compra de insumos no Lucro Real.",
        },
        {
            "prefixo": "Venda mercadoria revenda",
            "tipo_operacao": "VENDA",
            "tipo_produto": "REVENDA",
            "cfops": ["5102", "6102"],
            "permite_credito": False,
            "compoe_custo": False,
            "entra_dre": True,
            "observacoes": "Regra base de teste para venda de mercadorias no Lucro Real.",
        },
        {
            "prefixo": "Venda produto próprio",
            "tipo_operacao": "VENDA",
            "tipo_produto": "PROPRIO",
            "cfops": ["5101", "6101"],
            "permite_credito": False,
            "compoe_custo": False,
            "entra_dre": True,
            "observacoes": "Regra base de teste para venda de produção própria no Lucro Real.",
        },
    ]

    for empresa in Empresa.objects.all():
        tributos = {
            codigo: Tributo.objects.filter(empresa=empresa, codigo=codigo).first()
            for codigo in tributos_config
        }
        for operacao in operacoes:
            for cfop_codigo in operacao["cfops"]:
                cfop = Cfop.objects.filter(empresa=empresa, codigo=cfop_codigo).first()
                if not cfop:
                    continue
                for tributo_codigo, aliquota in tributos_config.items():
                    tributo = tributos.get(tributo_codigo)
                    if not tributo:
                        continue
                    nome = f"{operacao['prefixo']} {cfop_codigo} - {tributo_codigo}"
                    RegraTributaria.objects.update_or_create(
                        empresa=empresa,
                        nome=nome,
                        tributo=tributo,
                        cfop=cfop,
                        ncm=None,
                        defaults={
                            "tipo_operacao": operacao["tipo_operacao"],
                            "regime_tributario": "LUCRO_REAL",
                            "tipo_produto": operacao["tipo_produto"],
                            "uf_origem": None,
                            "uf_destino": None,
                            "cst_csosn": "",
                            "base_calculo": "VALOR_ITEM",
                            "aliquota": aliquota,
                            "reducao_base": Decimal("0.0000"),
                            "permite_credito": operacao["permite_credito"],
                            "compoe_custo": operacao["compoe_custo"],
                            "entra_dre": operacao["entra_dre"],
                            "ativo": True,
                            "vigencia_inicio": inicio,
                            "vigencia_fim": None,
                            "observacoes": operacao["observacoes"],
                        },
                    )


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0012_tributo_regratributaria_and_more"),
    ]

    operations = [
        migrations.RunPython(popular_regras_lucro_real_teste, migrations.RunPython.noop),
    ]

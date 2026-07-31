from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def _percentual(qtd, ordem):
    base = Decimal("100.000000") / Decimal(qtd)
    if ordem == qtd:
        usados = base.quantize(Decimal("0.000001")) * Decimal(qtd - 1)
        return Decimal("100.000000") - usados
    return base.quantize(Decimal("0.000001"))


def criar_prazos_e_formas(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    PrazoPagamento = apps.get_model("financeiro", "PrazoPagamento")
    PrazoPagamentoParcela = apps.get_model("financeiro", "PrazoPagamentoParcela")
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamentoParcela = apps.get_model("financeiro", "FormaPagamentoParcela")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")

    prazos = [
        ("AVISTA", "A vista", 1, 0, [0]),
        ("7D", "7 dias", 1, 7, [7]),
        ("30D", "30 dias", 1, 30, [30]),
        ("2X30", "2 parcelas - 30/60", 2, 30, [30, 60]),
        ("3X30", "3 parcelas - 30/60/90", 3, 30, [30, 60, 90]),
        ("4X30", "4 parcelas - 30/60/90/120", 4, 30, [30, 60, 90, 120]),
        ("6X30", "6 parcelas - 30/60/90/120/150/180", 6, 30, [30, 60, 90, 120, 150, 180]),
    ]

    for empresa in Empresa.objects.all():
        prazos_criados = {}
        for codigo, descricao, qtd, intervalo, dias in prazos:
            prazo, _ = PrazoPagamento.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "num_parcelas": qtd,
                    "intervalo_dias": intervalo,
                    "ativo": True,
                },
            )
            prazos_criados[codigo] = prazo
            for ordem, dia in enumerate(dias, start=1):
                PrazoPagamentoParcela.objects.update_or_create(
                    prazo=prazo,
                    ordem=ordem,
                    defaults={"dias": dia, "percentual": _percentual(qtd, ordem)},
                )

        conta = ContaBancaria.objects.filter(empresa=empresa, ativo=True).order_by("Idconta").first()
        formas = [
            ("DIN", "Dinheiro", "DINHEIRO", False, None, "AVISTA", Decimal("0.0000"), Decimal("0.00"), [0]),
            ("PIX", "Pix", "PIX", bool(conta), conta, "AVISTA", Decimal("0.0000"), Decimal("0.00"), [0]),
            ("DEB", "Cartao de debito", "DEBITO", bool(conta), conta, "AVISTA", Decimal("0.0000"), Decimal("0.00"), [1]),
            ("CCR", "Cartao credito rotativo", "CREDITO_ROTATIVO", bool(conta), conta, "30D", Decimal("0.0000"), Decimal("0.00"), [30]),
            ("CCP", "Cartao credito parcelado", "CREDITO_PARCELADO", bool(conta), conta, "6X30", Decimal("0.0000"), Decimal("0.00"), [30, 60, 90, 120, 150, 180]),
            ("BOL", "Boleto", "BOLETO", bool(conta), conta, "30D", Decimal("0.0000"), Decimal("0.00"), [30]),
            ("TRF", "Transferencia", "TRANSFERENCIA", bool(conta), conta, "AVISTA", Decimal("0.0000"), Decimal("0.00"), [0]),
        ]
        for codigo, descricao, tipo, recebivel, conta_liq, prazo_codigo, taxa, taxa_fixa, dias in formas:
            forma, _ = FormaPagamento.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "tipo": tipo,
                    "num_parcelas": len(dias),
                    "ativo": True,
                    "gera_recebivel_bancario": recebivel,
                    "conta_liquidacao": conta_liq if recebivel else None,
                    "prazo_pagamento": prazos_criados[prazo_codigo],
                    "prazo_credito_dias": max(dias),
                    "taxa_percentual": taxa,
                    "taxa_fixa": taxa_fixa,
                    "adquirente": None,
                    "tef_habilitado": False,
                    "tef_modalidade": "",
                    "tef_adquirente_codigo": "",
                    "tef_terminal_logico": "",
                },
            )
            for ordem, dia in enumerate(dias, start=1):
                FormaPagamentoParcela.objects.update_or_create(
                    forma=forma,
                    ordem=ordem,
                    defaults={
                        "dias": dia,
                        "percentual": _percentual(len(dias), ordem),
                        "valor_fixo": None,
                    },
                )

        legado_tipo = {
            "CRE": "CREDITO_ROTATIVO",
            "CRE2": "CREDITO_PARCELADO",
            "15D": "OUTRO",
            "15_30": "OUTRO",
            "15_30_45": "OUTRO",
            "TRC": "OUTRO",
        }
        for codigo, tipo in legado_tipo.items():
            FormaPagamento.objects.filter(empresa=empresa, codigo=codigo).update(tipo=tipo)


def remover_prazos_e_formas(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    PrazoPagamento = apps.get_model("financeiro", "PrazoPagamento")
    FormaPagamento.objects.filter(codigo__in=["DIN", "PIX", "DEB", "CCR", "CCP", "BOL", "TRF"]).delete()
    PrazoPagamento.objects.filter(codigo__in=["AVISTA", "7D", "30D", "2X30", "3X30", "4X30", "6X30"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0023_vinculos_contabeis_operacionais_unicos"),
    ]

    operations = [
        migrations.CreateModel(
            name="PrazoPagamento",
            fields=[
                ("Idprazo", models.BigAutoField(primary_key=True, serialize=False)),
                ("codigo", models.CharField(max_length=12)),
                ("descricao", models.CharField(max_length=120)),
                ("num_parcelas", models.PositiveIntegerField(default=1)),
                ("intervalo_dias", models.PositiveIntegerField(default=30)),
                ("ativo", models.BooleanField(default=True)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                ("empresa", models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="prazos_pagamento", to="cadastros.empresa")),
            ],
            options={
                "db_table": "financeiro_prazo_pagamento",
                "ordering": ["num_parcelas", "codigo"],
            },
        ),
        migrations.CreateModel(
            name="PrazoPagamentoParcela",
            fields=[
                ("Idprazoparcela", models.BigAutoField(primary_key=True, serialize=False)),
                ("ordem", models.PositiveIntegerField()),
                ("dias", models.PositiveIntegerField()),
                ("percentual", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                ("prazo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parcelas", to="financeiro.prazopagamento")),
            ],
            options={
                "db_table": "financeiro_prazo_pagamento_parcela",
                "ordering": ["prazo", "ordem"],
            },
        ),
        migrations.AddField(
            model_name="formapagamento",
            name="tipo",
            field=models.CharField(choices=[("DINHEIRO", "Dinheiro"), ("PIX", "Pix"), ("DEBITO", "Cartão de débito"), ("CREDITO_ROTATIVO", "Cartão crédito rotativo"), ("CREDITO_PARCELADO", "Cartão crédito parcelado"), ("BOLETO", "Boleto"), ("TRANSFERENCIA", "Transferência"), ("OUTRO", "Outro")], default="OUTRO", max_length=24),
        ),
        migrations.AddField(
            model_name="formapagamento",
            name="prazo_pagamento",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="formas_pagamento", to="financeiro.prazopagamento"),
        ),
        migrations.AddConstraint(
            model_name="prazopagamento",
            constraint=models.UniqueConstraint(fields=("empresa", "codigo"), name="uq_empresa_prazo_pagamento_codigo"),
        ),
        migrations.AddConstraint(
            model_name="prazopagamentoparcela",
            constraint=models.UniqueConstraint(fields=("prazo", "ordem"), name="uq_prazo_pagamento_parcela_ordem"),
        ),
        migrations.AddIndex(
            model_name="prazopagamentoparcela",
            index=models.Index(fields=["prazo", "ordem"], name="financeiro__prazo__76b8e4_idx"),
        ),
        migrations.RunPython(criar_prazos_e_formas, remover_prazos_e_formas),
    ]

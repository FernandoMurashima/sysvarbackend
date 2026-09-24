from decimal import Decimal

from django.db import migrations


def _normalizar_percentual(valor):
    if valor is None:
        return None
    return Decimal(str(valor)).quantize(Decimal("0.000001"))


def _assinatura(parcelas):
    return tuple(
        (int(parcela.dias or 0), _normalizar_percentual(parcela.percentual))
        for parcela in parcelas
    )


def _codigo_prazo_livre(PrazoPagamento, empresa_id, base):
    codigo = base[:12]
    if not PrazoPagamento.objects.filter(empresa_id=empresa_id, codigo=codigo).exists():
        return codigo
    for seq in range(1, 1000):
        sufixo = str(seq)
        candidato = f"{base[:12 - len(sufixo)]}{sufixo}"
        if not PrazoPagamento.objects.filter(empresa_id=empresa_id, codigo=candidato).exists():
            return candidato
    raise RuntimeError(f"Nao foi possivel gerar codigo de prazo livre para {base}")


def preservar_e_consolidar_parcelas(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    FormaPagamentoParcela = apps.get_model("financeiro", "FormaPagamentoParcela")
    PrazoPagamento = apps.get_model("financeiro", "PrazoPagamento")
    PrazoPagamentoParcela = apps.get_model("financeiro", "PrazoPagamentoParcela")

    q = schema_editor.quote_name
    schema_editor.execute(
        """
        CREATE TABLE IF NOT EXISTS {legado} AS
        SELECT
            p.*,
            f.{empresa_id} AS empresa_id,
            f.{codigo} AS forma_codigo,
            f.{descricao} AS forma_descricao,
            CURRENT_TIMESTAMP AS preservado_em
        FROM {parcelas} p
        JOIN {formas} f
          ON f.{forma_pk} = p.{forma_fk}
        """.format(
            legado=q("financeiro_forma_pagamento_parcela_legado"),
            parcelas=q("financeiro_forma_pagamento_parcela"),
            formas=q("financeiro_forma_pagamento"),
            empresa_id=q("empresa_id"),
            codigo=q("codigo"),
            descricao=q("descricao"),
            forma_pk=q("Idformapagamento"),
            forma_fk=q("forma_id"),
        )
    )

    for forma in FormaPagamento.objects.all().order_by("Idformapagamento"):
        parcelas = list(
            FormaPagamentoParcela.objects
            .filter(forma_id=forma.pk)
            .order_by("ordem", "Idformapagparcela")
        )
        if not parcelas:
            continue

        assinatura = _assinatura(parcelas)
        prazo = forma.prazo_pagamento
        if prazo:
            parcelas_prazo = list(PrazoPagamentoParcela.objects.filter(prazo=prazo).order_by("ordem"))
            if _assinatura(parcelas_prazo) != assinatura:
                prazo = None

        if not prazo:
            for candidato in PrazoPagamento.objects.filter(empresa_id=forma.empresa_id).order_by("Idprazo"):
                parcelas_candidato = list(PrazoPagamentoParcela.objects.filter(prazo=candidato).order_by("ordem"))
                if _assinatura(parcelas_candidato) == assinatura:
                    prazo = candidato
                    break

        if not prazo:
            codigo_base = f"FP{forma.codigo or forma.pk}".replace("/", "").replace(" ", "").upper()
            codigo = _codigo_prazo_livre(PrazoPagamento, forma.empresa_id, codigo_base)
            prazo = PrazoPagamento.objects.create(
                empresa_id=forma.empresa_id,
                codigo=codigo,
                descricao=f"Condicao migrada de {forma.codigo} - {forma.descricao}"[:120],
                finalidade="AMBOS",
                num_parcelas=len(parcelas),
                intervalo_dias=int(parcelas[0].dias or 0) if len(parcelas) == 1 else 30,
                ativo=forma.ativo,
            )
            for ordem, parcela in enumerate(parcelas, start=1):
                PrazoPagamentoParcela.objects.create(
                    prazo=prazo,
                    ordem=ordem,
                    dias=max(0, int(parcela.dias or 0)),
                    percentual=parcela.percentual,
                )

        forma.prazo_pagamento_id = prazo.pk
        forma.num_parcelas = prazo.num_parcelas
        forma.save(update_fields=["prazo_pagamento", "num_parcelas"])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("financeiro", "0027_financeiro_fase1_pagamentos_prazos"),
    ]

    operations = [
        migrations.RunPython(preservar_e_consolidar_parcelas, migrations.RunPython.noop),
        migrations.DeleteModel(name="FormaPagamentoParcela"),
    ]

from django.db import migrations
from django.db.models import Max


def _to_int(value):
    try:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0
    except (TypeError, ValueError):
        return 0


def reconciliar_sequencias(apps, schema_editor):
    Loja = apps.get_model("cadastros", "Loja")
    NFeDevolucao = apps.get_model("fiscal", "NFeDevolucao")
    NotaFiscalSaida = apps.get_model("fiscal", "NotaFiscalSaida")

    for loja in Loja.objects.all().iterator():
        serie = str(loja.serie_nfe or 1)
        ambiente = loja.ambiente_fiscal or "HOMOLOGACAO"
        numeros = []

        devolucao_max = (
            NFeDevolucao.objects.filter(
                loja_id=loja.pk,
                ambiente=ambiente,
                modelo="55",
                serie=_to_int(serie),
            )
            .aggregate(max_numero=Max("numero"))
            .get("max_numero")
            or 0
        )
        numeros.append(_to_int(devolucao_max))

        saida_numeros = NotaFiscalSaida.objects.filter(
            loja_origem_id=loja.pk,
            modelo="55",
            serie=serie,
        ).values_list("numero", flat=True)
        numeros.extend(_to_int(numero) for numero in saida_numeros)

        maior = max(numeros or [0])
        atual = _to_int(loja.proximo_numero_nfe) or 1
        if atual <= maior:
            loja.proximo_numero_nfe = maior + 1
            loja.save(update_fields=["proximo_numero_nfe"])


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0041_nfe_devolucao_loja_status"),
    ]

    operations = [
        migrations.RunPython(reconciliar_sequencias, migrations.RunPython.noop),
    ]

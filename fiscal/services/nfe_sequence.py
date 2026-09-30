from django.db.models import Max

from cadastros.models import Loja
from fiscal.models import NFeDevolucao, NotaFiscalSaida


def _to_int(value) -> int:
    try:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0
    except (TypeError, ValueError):
        return 0


def maior_numero_nfe_utilizado(loja: Loja, *, serie=None, ambiente=None) -> int:
    serie = str(serie if serie is not None else loja.serie_nfe or 1)
    ambiente = str(ambiente if ambiente is not None else loja.ambiente_fiscal or "HOMOLOGACAO")
    numeros = []

    devolucao_max = (
        NFeDevolucao.objects.filter(
            loja=loja,
            ambiente=ambiente,
            modelo="55",
            serie=_to_int(serie),
        )
        .aggregate(max_numero=Max("numero"))
        .get("max_numero")
        or 0
    )
    numeros.append(_to_int(devolucao_max))

    # NotaFiscalSaida usa a mesma sequencia da loja emissora, mas ainda nao
    # possui ambiente fiscal persistido no modelo.
    saida_numeros = NotaFiscalSaida.objects.filter(
        loja_origem=loja,
        modelo="55",
        serie=serie,
    ).values_list("numero", flat=True)
    numeros.extend(_to_int(numero) for numero in saida_numeros)

    return max(numeros or [0])


def reconciliar_proximo_numero_nfe_loja(loja: Loja) -> Loja:
    maior = maior_numero_nfe_utilizado(loja)
    atual = _to_int(loja.proximo_numero_nfe) or 1
    esperado = max(atual, maior + 1)
    if esperado != atual:
        loja.proximo_numero_nfe = esperado
        loja.save(update_fields=["proximo_numero_nfe"])
    return loja


def reservar_proximo_numero_nfe(loja: Loja):
    loja = Loja.objects.select_for_update().get(pk=loja.pk)
    serie = int(loja.serie_nfe or 1)
    ambiente = loja.ambiente_fiscal or "HOMOLOGACAO"
    maior = maior_numero_nfe_utilizado(loja, serie=serie, ambiente=ambiente)
    numero = max(_to_int(loja.proximo_numero_nfe) or 1, maior + 1)
    loja.proximo_numero_nfe = numero + 1
    loja.save(update_fields=["proximo_numero_nfe"])
    return serie, numero, ambiente, loja

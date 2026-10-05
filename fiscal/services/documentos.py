from financeiro.models import SequenciaDocumento
from financeiro.services import escopo_empresa, reservar_numero_documento


LIMITE_DOCUMENTO_DEVOLUCAO = 9999999


def formatar_documento_devolucao(numero):
    try:
        sequencial = int(numero)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sequencial invalido para documento da devolucao.") from exc
    if sequencial <= 0 or sequencial > LIMITE_DOCUMENTO_DEVOLUCAO:
        raise ValueError("Sequencial invalido para documento da devolucao.")
    return f"DEV-{sequencial:07d}"


def reservar_documento_devolucao(empresa):
    if not empresa:
        raise ValueError("Empresa obrigatoria para gerar documento da devolucao.")
    numero = reservar_numero_documento(
        empresa,
        SequenciaDocumento.TIPO_DEVOLUCAO,
        escopo_empresa(),
    )
    return formatar_documento_devolucao(numero)

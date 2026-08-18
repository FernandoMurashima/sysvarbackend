from rest_framework import serializers


def normalizar_chave_acesso_nfe(chave):
    chave = str(chave or "").strip()
    if not chave:
        return ""
    if not chave.isdigit():
        raise serializers.ValidationError("Chave de acesso deve conter somente números.")
    if len(chave) != 44:
        raise serializers.ValidationError("Chave de acesso deve conter exatamente 44 dígitos.")
    if not chave_acesso_nfe_dv_valido(chave):
        raise serializers.ValidationError("Dígito verificador da chave de acesso inválido.")
    return chave


def chave_acesso_nfe_dv_valido(chave):
    if len(chave) != 44 or not chave.isdigit():
        return False
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = 0
    for index, digito in enumerate(reversed(chave[:43])):
        soma += int(digito) * pesos[index % len(pesos)]
    resto = soma % 11
    dv = 11 - resto
    if dv >= 10:
        dv = 0
    return dv == int(chave[-1])

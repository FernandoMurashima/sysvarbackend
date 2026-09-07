from rest_framework.exceptions import ValidationError

from fiscal.models import NotaFiscalEntrada
from fiscal.validators import normalizar_chave_acesso_nfe


MSG_CHAVE_DUPLICADA = "NF-e já registrada no Sysvar. Chave de acesso já utilizada."
MSG_DOCUMENTO_DUPLICADO = "Já existe uma nota fiscal deste fornecedor com o mesmo modelo, série e número."


def normalizar_identidade_nota_entrada(data, validar_chave=True):
    if "modelo" in data and data.get("modelo") is not None:
        data["modelo"] = str(data.get("modelo") or "").strip() or "55"
    if "serie" in data and data.get("serie") is not None:
        data["serie"] = str(data.get("serie") or "").strip()
    if "numero" in data and data.get("numero") is not None:
        data["numero"] = str(data.get("numero") or "").strip()
    if validar_chave and "chave_acesso" in data and data.get("chave_acesso") is not None:
        data["chave_acesso"] = normalizar_chave_acesso_nfe(data.get("chave_acesso")) or None
    elif "chave_acesso" in data and data.get("chave_acesso") is not None:
        data["chave_acesso"] = str(data.get("chave_acesso") or "").strip() or None
    return data


def validar_duplicidade_nota_entrada(data, instance=None, bloquear_linha=False, validar_chave=True):
    data = normalizar_identidade_nota_entrada(data, validar_chave=validar_chave)
    chave = data.get("chave_acesso")
    if chave:
        qs = NotaFiscalEntrada.objects.all()
        if bloquear_linha:
            qs = qs.select_for_update()
        qs = qs.filter(chave_acesso=chave)
        if instance:
            qs = qs.exclude(pk=instance.pk)
        existente = qs.select_related("fornecedor").first()
        if existente:
            detail = MSG_CHAVE_DUPLICADA
            if not data.get("empresa") or getattr(data.get("empresa"), "id", None) == existente.empresa_id:
                fornecedor = getattr(existente.fornecedor, "nome_fornecedor", "") or "fornecedor informado"
                detail = f"{detail} NF-e {existente.numero}, série {existente.serie}, do fornecedor {fornecedor}, já está registrada. Status: {existente.get_status_display()}."
            raise ValidationError({"chave_acesso": detail})

    empresa = data.get("empresa")
    fornecedor = data.get("fornecedor")
    empresa_id = getattr(empresa, "id", None) or data.get("empresa_id")
    fornecedor_id = getattr(fornecedor, "id", None) or data.get("fornecedor_id")
    modelo = str(data.get("modelo") or "55").strip()
    serie = str(data.get("serie") or "").strip()
    numero = str(data.get("numero") or "").strip()
    if not (empresa_id and fornecedor_id and modelo and numero):
        return data
    qs = NotaFiscalEntrada.objects.all()
    if bloquear_linha:
        qs = qs.select_for_update()
    qs = qs.filter(empresa_id=empresa_id, fornecedor_id=fornecedor_id, modelo=modelo, serie=serie, numero=numero)
    if instance:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        raise ValidationError({"numero": MSG_DOCUMENTO_DUPLICADO})
    return data


def validation_error_from_integrity_error(exc):
    text = str(exc).lower()
    if "chave_acesso" in text:
        return ValidationError({"chave_acesso": MSG_CHAVE_DUPLICADA})
    if "uq_fiscal_nfe_emp_forn_doc" in text or "empresa" in text and "fornecedor" in text:
        return ValidationError({"numero": MSG_DOCUMENTO_DUPLICADO})
    return ValidationError({"detail": "Nota fiscal de entrada duplicada."})

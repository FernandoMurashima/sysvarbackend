"""Controle de metadados para futura integracao com HubTokenAuthentication.

Nao recebe arquivos, nao valida conteudo e nao publica endpoints. O chamador deve
usar o SysvarHub autenticado pela Central, nunca um hub_id recebido no payload.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import HubBackup, SysvarHub


@transaction.atomic
def registrar_metadados_backup(*, hub, **metadados):
    if not isinstance(hub, SysvarHub) or not hub.pk:
        raise ValidationError("Hub autenticado obrigatorio.")
    permitido = set(HubBackup.METADADOS) - {"hub_id", "empresa_id", "loja_id", "chave_armazenamento"}
    if set(metadados) - permitido:
        raise ValidationError("Somente metadados de backup sao aceitos; escopo e estados pertencem a Central.")
    oficial = SysvarHub.objects.select_for_update().select_related("loja").filter(pk=hub.pk, ativo=True).first()
    if not oficial:
        raise ValidationError("Hub autenticado indisponivel.")
    candidato = HubBackup(hub=oficial, empresa_id=oficial.loja.empresa_id, loja_id=oficial.loja_id, **metadados)
    candidato.preparar_metadados(oficial)
    # Normalizar datas/numeros como o model antes de comparar um retry.
    for field in candidato._meta.concrete_fields:
        if field.attname in HubBackup.METADADOS:
            converter = field.target_field if field.is_relation else field
            setattr(candidato, field.attname, converter.to_python(getattr(candidato, field.attname)))
    candidato.full_clean(validate_unique=False, validate_constraints=False)
    anterior = HubBackup.objects.select_for_update().filter(backup_uuid=candidato.backup_uuid).first()
    if anterior:
        if any(getattr(anterior, key) != getattr(candidato, key) for key in HubBackup.METADADOS):
            raise ValidationError("Identificador de backup reutilizado com identidade ou manifesto divergente.")
        return anterior, False
    candidato.save(force_insert=True)
    return candidato, True

from django.core.exceptions import ValidationError
from django.db import transaction

from auditoria.models import AuditAction, AuditCategory, AuditOrigin
from auditoria.services import AuditService, instance_snapshot
from cadastros.models import Cliente


class ClientePadraoService:
    nome = "Consumidor Final"
    apelido = "Cliente nao identificado"
    documento = Cliente.DOCUMENTO_CONSUMIDOR_FINAL

    @classmethod
    def obter_ou_criar(cls, empresa, user=None, request=None, aplicar=True):
        if not empresa:
            raise ValidationError("Informe a empresa.")
        with transaction.atomic():
            qs = Cliente.objects.select_for_update().filter(empresa=empresa)
            marcados = list(qs.filter(cliente_padrao=True))
            documento_000 = list(qs.filter(documento=cls.documento))
            candidatos = {c.pk: c for c in marcados + documento_000 if c.pk}
            if len(marcados) > 1 or len(documento_000) > 1 or len(candidatos) > 1:
                AuditService.required(
                    action=AuditAction.CLIENT_STANDARD_CONFLICT,
                    category=AuditCategory.CADASTRO,
                    origin=AuditOrigin.COMMAND,
                    empresa=empresa,
                    user=user,
                    request=request,
                    result="FAILURE",
                    severity="ERROR",
                    app_label="cadastros",
                    model="cliente",
                    metadata={
                        "marcados": [c.pk for c in marcados],
                        "documento_000": [c.pk for c in documento_000],
                    },
                )
                raise ValidationError("Conflito no cliente padrão da empresa.")
            if marcados:
                cliente = marcados[0]
                if cliente.documento != cls.documento or cliente.tipo_pessoa != Cliente.TIPO_PESSOA_FISICA:
                    raise ValidationError("Cliente padrão existente possui dados incompatíveis.")
                return cliente, False
            if documento_000:
                cliente = documento_000[0]
                if not aplicar:
                    return cliente, False
                if not cliente.cliente_padrao:
                    cliente.cliente_padrao = True
                    cliente.tipo_pessoa = Cliente.TIPO_PESSOA_FISICA
                    cliente.ativo = True
                    cliente.bloqueio = False
                    cliente.save(update_fields=["cliente_padrao", "tipo_pessoa", "ativo", "bloqueio", "cpf", "documento"])
                return cliente, False
            if not aplicar:
                return None, False
            cliente = Cliente.objects.create(
                empresa=empresa,
                tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
                documento=cls.documento,
                cpf=cls.documento,
                cliente_padrao=True,
                nome_cliente=cls.nome,
                apelido=cls.apelido,
                ativo=True,
                bloqueio=False,
                aceita_email=False,
                aceita_whatsapp=False,
                aceita_sms=False,
            )
            AuditService.required_success(
                AuditAction.CLIENT_STANDARD_CREATED,
                category=AuditCategory.CADASTRO,
                origin=AuditOrigin.COMMAND,
                empresa=empresa,
                user=user,
                request=request,
                instance=cliente,
                after=instance_snapshot(cliente),
            )
            return cliente, True

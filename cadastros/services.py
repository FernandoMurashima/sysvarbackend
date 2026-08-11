from django.core.exceptions import ValidationError
from django.db import transaction

from auditoria.models import AuditAction, AuditCategory, AuditOrigin
from auditoria.services import AuditService, instance_snapshot
from cadastros.models import Cargo, Cliente


CARGOS_FUNCIONARIOS_INICIAIS = [
    {
        "codigo": "VENDEDOR",
        "descricao": "Vendedor",
        "participa_vendas": True,
        "permite_comissao": True,
        "autoridade_operacional_loja": True,
        "permite_multiplas_lojas": False,
        "gerencial": False,
    },
    {
        "codigo": "CAIXA",
        "descricao": "Caixa",
        "participa_vendas": False,
        "permite_comissao": False,
        "autoridade_operacional_loja": True,
        "permite_multiplas_lojas": False,
        "gerencial": False,
    },
    {
        "codigo": "GERENTE",
        "descricao": "Gerente",
        "participa_vendas": False,
        "permite_comissao": True,
        "autoridade_operacional_loja": True,
        "permite_multiplas_lojas": False,
        "gerencial": True,
    },
    {
        "codigo": "SUPERVISOR",
        "descricao": "Supervisor",
        "participa_vendas": False,
        "permite_comissao": True,
        "autoridade_operacional_loja": True,
        "permite_multiplas_lojas": True,
        "gerencial": True,
    },
    {"codigo": "ASSISTENTE", "descricao": "Assistente"},
    {"codigo": "AUXILIAR", "descricao": "Auxiliar"},
    {"codigo": "AUXADM", "descricao": "Auxiliar Administrativo"},
    {"codigo": "ASSADM", "descricao": "Assistente Administrativo"},
    {"codigo": "ASSFIN", "descricao": "Assistente Financeiro"},
    {"codigo": "AUXFIN", "descricao": "Auxiliar Financeiro"},
    {"codigo": "COMPRADOR", "descricao": "Comprador"},
    {
        "codigo": "ESTOQUISTA",
        "descricao": "Estoquista",
        "autoridade_operacional_loja": True,
    },
    {
        "codigo": "ALMOX",
        "descricao": "Almoxarife",
        "autoridade_operacional_loja": True,
    },
    {
        "codigo": "CONFERENTE",
        "descricao": "Conferente",
        "autoridade_operacional_loja": True,
    },
    {
        "codigo": "RECEBEDOR",
        "descricao": "Recebedor",
        "autoridade_operacional_loja": True,
    },
    {"codigo": "COSTUREIRA", "descricao": "Costureira"},
    {"codigo": "AUXPROD", "descricao": "Auxiliar de Produção"},
]


class CargoInicialService:
    @classmethod
    def garantir_basicos(cls, empresa):
        if not empresa:
            raise ValidationError("Informe a empresa.")
        criados = []
        for item in CARGOS_FUNCIONARIOS_INICIAIS:
            defaults = {
                "descricao": item["descricao"],
                "ativo": True,
                "participa_vendas": item.get("participa_vendas", False),
                "permite_comissao": item.get("permite_comissao", False),
                "autoridade_operacional_loja": item.get("autoridade_operacional_loja", False),
                "permite_multiplas_lojas": item.get("permite_multiplas_lojas", False),
                "gerencial": item.get("gerencial", False),
            }
            cargo, created = Cargo.objects.get_or_create(
                empresa=empresa,
                codigo=item["codigo"],
                defaults=defaults,
            )
            if created:
                criados.append(cargo)
        return criados


class ClientePadraoService:
    nome = "Consumidor Final"
    apelido = "Cliente nao ident."
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

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from cadastros.models import EmpresaContrato, EmpresaModulo, ModuloSistema
from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission


NONE = UserModulePermission.Access.NONE
VIEW = UserModulePermission.Access.VIEW
EDIT = UserModulePermission.Access.EDIT
BASIC_MODULES = {"operacional", "cadastros", "produtos", "configuracoes"}
COMMERCIAL_MODULES = {"vendas", "compras", "estoque", "financeiro", "fiscal", "producao", "distribuicao", "relatorios"}


@dataclass(frozen=True)
class ContractState:
    active: bool
    reason: str = ""


def audit_event(action, request=None, user=None, model="security", object_id="", changes=None):
    from auditoria.models import AuditAction, AuditCategory, AuditResult, AuditSeverity
    from auditoria.services import AuditService

    action_name = AuditAction.LEGACY_MAP.get(str(action), str(action).upper())
    category = AuditCategory.SECURITY
    result = AuditResult.SUCCESS
    severity = AuditSeverity.INFO
    if "denied" in str(action) or "block" in str(action):
        result = AuditResult.DENIED
        severity = AuditSeverity.WARNING
    if "contract" in str(action):
        category = AuditCategory.CONTRACT
    elif "profile" in str(action) or "permission" in str(action) or "master" in str(action):
        category = AuditCategory.ACCESS
    elif "user" in str(action):
        category = AuditCategory.USER_MANAGEMENT
    AuditService.record(
        action=action_name,
        category=category,
        result=result,
        severity=severity,
        request=request,
        user=user,
        app_label="accounts",
        model=model,
        object_id=object_id,
        metadata=changes or {},
    )


def increment_permissions_version(empresa):
    try:
        contrato = empresa.contrato
    except EmpresaContrato.DoesNotExist:
        return
    contrato.incrementar_versao()


def sync_legacy_license_flags(empresa):
    try:
        contrato = empresa.contrato
    except EmpresaContrato.DoesNotExist:
        contrato = None
    module_map = {
        "vendas": "usa_vendas",
        "compras": "usa_compras",
        "estoque": "usa_estoque",
        "financeiro": "usa_financeiro",
        "fiscal": "usa_fiscal",
        "producao": "usa_producao",
        "distribuicao": "usa_distribuicao_producao",
    }
    updates = {}
    if contrato:
        updates["plano_completo"] = bool(contrato.plano_completo)
        updates["licenca_master"] = bool(contrato.plano_completo)
    contracted = set(
        EmpresaModulo.objects.filter(empresa=empresa, contratado=True, modulo__chave__in=module_map)
        .values_list("modulo__chave", flat=True)
    )
    for key, field in module_map.items():
        updates[field] = key in contracted or bool(contrato and contrato.plano_completo)
    if updates.get("usa_producao"):
        updates["usa_ficha_tecnica"] = True
        updates["usa_faccao"] = True
        updates["usa_distribuicao_producao"] = True
    else:
        updates["usa_ficha_tecnica"] = False
        updates["usa_faccao"] = False
    changed = {field: value for field, value in updates.items() if getattr(empresa, field, None) != value}
    if changed:
        for field, value in changed.items():
            setattr(empresa, field, value)
        empresa.save(update_fields=list(changed.keys()))


def sync_empresa_modulos_from_legacy_flags(empresa):
    module_map = {
        "vendas": "usa_vendas",
        "compras": "usa_compras",
        "estoque": "usa_estoque",
        "financeiro": "usa_financeiro",
        "fiscal": "usa_fiscal",
        "producao": "usa_producao",
        "distribuicao": "usa_distribuicao_producao",
    }
    modules = {m.chave: m for m in ModuloSistema.objects.filter(chave__in=module_map)}
    for key, field in module_map.items():
        modulo = modules.get(key)
        if not modulo:
            continue
        EmpresaModulo.objects.update_or_create(
            empresa=empresa,
            modulo=modulo,
            defaults={"contratado": bool(getattr(empresa, field, False) or getattr(empresa, "plano_completo", False))},
        )
    increment_permissions_version(empresa)


class CompanyModuleService:
    def __init__(self, empresa):
        self.empresa = empresa

    def contract(self):
        try:
            return self.empresa.contrato
        except EmpresaContrato.DoesNotExist:
            return None

    def contract_state(self) -> ContractState:
        if not self.empresa:
            return ContractState(False, "Usuário sem empresa vinculada.")
        if not self.empresa.ativo:
            return ContractState(False, "Empresa inativa.")
        contrato = self.contract()
        if not contrato:
            return ContractState(False, "Empresa sem contrato.")
        today = timezone.localdate()
        if contrato.status != EmpresaContrato.STATUS_ATIVO:
            return ContractState(False, f"Contrato {contrato.get_status_display().lower()}.")
        if contrato.data_inicio and contrato.data_inicio > today:
            return ContractState(False, "Contrato ainda não iniciado.")
        if contrato.data_fim and contrato.data_fim < today:
            return ContractState(False, "Contrato vencido.")
        if contrato.limite_sessoes_simultaneas < 1:
            return ContractState(False, "Contrato sem licenças.")
        return ContractState(True, "")

    def available_module_keys(self) -> set[str]:
        state = self.contract_state()
        if not state.active:
            return set()
        contrato = self.contract()
        keys = set(
            ModuloSistema.objects.filter(ativo=True, basico=True).values_list("chave", flat=True)
        )
        if contrato and contrato.plano_completo:
            keys.update(
                ModuloSistema.objects.filter(ativo=True, basico=False, categoria=ModuloSistema.CATEGORIA_COMERCIAL)
                .values_list("chave", flat=True)
            )
            return keys
        today = timezone.localdate()
        contratados = (
            EmpresaModulo.objects
            .filter(empresa=self.empresa, contratado=True, modulo__ativo=True)
            .filter(Q(data_inicio__isnull=True) | Q(data_inicio__lte=today))
            .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=today))
        )
        keys.update(contratados.values_list("modulo__chave", flat=True))
        return keys

    def module_available(self, module_key: str) -> bool:
        return module_key in self.available_module_keys()


class LicenseService:
    def __init__(self, empresa):
        self.empresa = empresa

    def usage(self):
        contrato = self.empresa.contrato
        cutoff = timezone.now() - timezone.timedelta(minutes=getattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30))
        used = self.empresa.sessoes_usuarios.filter(ativa=True, ultima_atividade_em__gte=cutoff).count()
        limit = int(contrato.limite_sessoes_simultaneas or 0)
        return {
            "limite_sessoes_simultaneas": limit,
            "sessoes_ativas": used,
            "sessoes_disponiveis": max(0, limit - used),
            "limite_excedido": used > limit,
            "limite_usuarios": limit,
            "usuarios_ativos": self.empresa.usuarios.filter(is_active=True, is_superuser=False).count(),
            "licencas_disponiveis": max(0, limit - used),
            "excedido": used > limit,
        }

    def assert_can_consume(self):
        return EmpresaContrato.objects.select_for_update().get(empresa=self.empresa)


class EffectiveAccessService:
    def __init__(self, user):
        self.user = user
        self._available = None
        self._contract_state = None

    def contract_state(self) -> ContractState:
        if self._contract_state is not None:
            return self._contract_state
        user = self.user
        if not user or not getattr(user, "is_authenticated", False):
            self._contract_state = ContractState(False, "Usuário não autenticado.")
        elif user.is_superuser:
            self._contract_state = ContractState(True, "")
        elif not user.is_active:
            self._contract_state = ContractState(False, "Usuário inativo.")
        elif not user.empresa_id:
            self._contract_state = ContractState(False, "Usuário sem empresa vinculada.")
        else:
            self._contract_state = CompanyModuleService(user.empresa).contract_state()
        return self._contract_state

    def available_modules(self) -> set[str]:
        if self.user and self.user.is_superuser:
            return set(ModuloSistema.objects.filter(ativo=True).values_list("chave", flat=True))
        if self._available is None:
            self._available = CompanyModuleService(self.user.empresa).available_module_keys() if getattr(self.user, "empresa_id", None) else set()
        return self._available

    def is_company_master(self) -> bool:
        user = self.user
        if not user or user.is_superuser or not getattr(user, "empresa_id", None):
            return False
        try:
            contrato = EmpresaContrato.objects.only("usuario_master_id").get(empresa_id=user.empresa_id)
        except EmpresaContrato.DoesNotExist:
            return False
        return contrato.usuario_master_id == user.id and user.is_active

    def module_access(self, module_key: str | None):
        if not module_key:
            return NONE
        user = self.user
        if not user or not getattr(user, "is_authenticated", False):
            return NONE
        if user.is_superuser:
            return EDIT
        if not self.contract_state().active:
            return NONE
        if module_key not in self.available_modules():
            return NONE
        if self.is_company_master():
            return EDIT
        override = user.module_permissions.filter(modulo=module_key).only("acesso").first()
        if override:
            return override.acesso
        perfil = getattr(user, "perfil_principal", None)
        if not perfil or not perfil.ativo:
            return NONE
        perm = PerfilModuloPermissao.objects.filter(perfil=perfil, modulo__chave=module_key).select_related("modulo").first()
        return perm.acesso if perm else NONE

    def has_module_access(self, module_keys: str | Iterable[str], required=VIEW):
        keys = [module_keys] if isinstance(module_keys, str) else list(module_keys or [])
        if not keys:
            return False
        for key in keys:
            access = self.module_access(key)
            if required == EDIT and access != EDIT:
                return False
            if required == VIEW and access not in {VIEW, EDIT}:
                return False
        return True

    def allowed_store_ids(self):
        user = self.user
        if not user or not getattr(user, "is_authenticated", False):
            return []
        if user.is_superuser:
            return None
        ids = set(user.lojas.values_list("id", flat=True))
        if user.loja_id:
            ids.add(user.loja_id)
        return sorted(ids)

    def can_access_store(self, loja):
        user = self.user
        if user.is_superuser:
            return True
        if not loja or loja.empresa_id != user.empresa_id:
            return False
        allowed = self.allowed_store_ids()
        return allowed is None or loja.id in allowed

    def effective_permissions_payload(self):
        return {key: self.module_access(key) for key in sorted(self.available_modules())}

    def session_payload(self):
        user = self.user
        contrato = None
        if getattr(user, "empresa_id", None):
            try:
                c = user.empresa.contrato
                usage = LicenseService(user.empresa).usage()
                contrato = {
                    "status": c.status,
                    "data_inicio": c.data_inicio,
                    "data_fim": c.data_fim,
                    "limite_usuarios": c.limite_usuarios,
                    "limite_sessoes_simultaneas": c.limite_sessoes_simultaneas,
                    "usuarios_ativos": usage["usuarios_ativos"],
                    "sessoes_ativas": usage["sessoes_ativas"],
                    "licencas_disponiveis": usage["licencas_disponiveis"],
                    "sessoes_disponiveis": usage["sessoes_disponiveis"],
                    "excedido": usage["excedido"],
                    "limite_excedido": usage["limite_excedido"],
                    "plano_completo": c.plano_completo,
                    "permissions_version": c.permissions_version,
                }
            except EmpresaContrato.DoesNotExist:
                contrato = None
        return {
            "is_platform_superuser": bool(getattr(user, "is_superuser", False)),
            "is_company_master": self.is_company_master(),
            "contrato": contrato,
            "loja_principal": {
                "id": user.loja_id,
                "nome_loja": getattr(user.loja, "nome_loja", None),
                "apelido_loja": getattr(user.loja, "apelido_loja", None),
            } if getattr(user, "loja_id", None) else None,
            "perfil_principal": {
                "id": user.perfil_principal_id,
                "nome": getattr(user.perfil_principal, "nome", None),
            } if getattr(user, "perfil_principal_id", None) else None,
            "permissoes_administrativas": {
                "usuarios_gerenciar": self.is_company_master() or self.has_module_access("operacional", EDIT),
                "perfis_gerenciar": self.is_company_master() or self.has_module_access("configuracoes", EDIT),
            },
            "modulos_disponiveis_empresa": sorted(self.available_modules()) if getattr(user, "is_authenticated", False) else [],
            "permissoes_efetivas": self.effective_permissions_payload() if getattr(user, "is_authenticated", False) else {},
            "lojas_permitidas": list(user.lojas.values("id", "nome_loja", "apelido_loja")) if getattr(user, "is_authenticated", False) and not user.is_superuser else [],
            "sessao_atual": self.current_session_payload(),
        }

    def current_session_payload(self):
        sessao = getattr(self.user, "_current_access_session", None)
        if not sessao:
            return None
        return {
            "session_id": str(sessao.session_id),
            "dispositivo_id": sessao.dispositivo_id,
            "iniciada_em": sessao.iniciada_em,
            "ultima_atividade_em": sessao.ultima_atividade_em,
        }


class MasterTransferService:
    def __init__(self, actor, empresa, new_master, request=None):
        self.actor = actor
        self.empresa = empresa
        self.new_master = new_master
        self.request = request

    def transfer(self):
        with transaction.atomic():
            contrato = EmpresaContrato.objects.select_for_update().get(empresa=self.empresa)
            if not (self.actor.is_superuser or contrato.usuario_master_id == self.actor.id):
                audit_event("master_transfer_denied", self.request, self.actor, "empresa", self.empresa.pk, {"novo_master": self.new_master.pk})
                raise PermissionDenied("Somente superusuário ou master atual pode transferir o master.")
            if self.new_master.empresa_id != self.empresa.id or not self.new_master.is_active or self.new_master.is_superuser:
                raise ValidationError({"usuario_master": "Novo master inválido para esta empresa."})
            old = contrato.usuario_master_id
            contrato.usuario_master = self.new_master
            contrato.incrementar_versao(save=False)
            contrato.save(update_fields=["usuario_master", "permissions_version", "updated_at"])
            transaction.on_commit(lambda: audit_event("master_transfer", self.request, self.actor, "empresa", self.empresa.pk, {"old": old, "new": self.new_master.pk}))
            return contrato


class ProfileDefaultService:
    def __init__(self, actor, perfil, request=None):
        self.actor = actor
        self.perfil = perfil
        self.request = request

    def assert_can_manage(self):
        if self.actor.is_superuser:
            return
        if self.perfil.empresa_id != getattr(self.actor, "empresa_id", None):
            raise PermissionDenied("Perfil pertence a outra empresa.")
        access = EffectiveAccessService(self.actor)
        if not access.is_company_master() and not access.has_module_access("configuracoes", EDIT):
            raise PermissionDenied("Sem permissão para gerenciar perfis.")

    def set_default(self):
        self.assert_can_manage()
        with transaction.atomic():
            perfil = PerfilAcesso.objects.select_for_update().select_related("empresa").get(pk=self.perfil.pk)
            if not perfil.ativo:
                raise ValidationError({"padrao": "Perfil inativo não pode ser definido como padrão."})
            PerfilAcesso.objects.select_for_update().filter(
                empresa=perfil.empresa,
                ativo=True,
                padrao=True,
            ).exclude(pk=perfil.pk).update(padrao=False)
            if not perfil.padrao:
                perfil.padrao = True
                perfil.save(update_fields=["padrao", "updated_at"])
            increment_permissions_version(perfil.empresa)
            transaction.on_commit(lambda: audit_event("profile_set_default", self.request, self.actor, "perfil_acesso", perfil.pk))
            return perfil

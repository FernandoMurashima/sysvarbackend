from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.services.effective_access import EDIT, NONE, VIEW, EffectiveAccessService


ADMIN_TYPES = {"Admin", "Administrador"}

APP_MODULE_MAP = {
    "accounts": "operacional",
    "auth": "configuracoes",
    "authtoken": "configuracoes",
    "cadastros": "cadastros",
    "produto": "produtos",
    "fiscal": "fiscal",
    "compras": "compras",
    "financeiro": "financeiro",
    "relatorios": "relatorios",
    "distribuicao": "distribuicao",
    "auditoria": "auditoria",
}

EMPRESA_FIELD_MODULE_MAP = {
    "usa_vendas": "vendas",
    "usa_compras": "compras",
    "usa_estoque": "estoque",
    "usa_financeiro": "financeiro",
    "usa_fiscal": "fiscal",
    "usa_producao": "producao",
    "usa_ficha_tecnica": "producao",
    "usa_faccao": "producao",
    "usa_distribuicao_producao": "distribuicao",
}


def user_type(user) -> str:
    return (getattr(user, "type", "") or "").strip()


def has_role(user, allowed_roles) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    current = user_type(user)
    return current in set(allowed_roles or [])


def module_key_for_view(view):
    explicit = getattr(view, "module_key", None)
    if explicit:
        return explicit
    empresa_field = getattr(view, "empresa_modulo_field", None)
    if empresa_field in EMPRESA_FIELD_MODULE_MAP:
        return EMPRESA_FIELD_MODULE_MAP[empresa_field]
    model = getattr(getattr(view, "queryset", None), "model", None)
    app_label = getattr(getattr(model, "_meta", None), "app_label", "")
    return APP_MODULE_MAP.get(app_label)


def module_keys_for_read(view):
    keys = getattr(view, "read_module_keys", None)
    if keys:
        return [key for key in keys if key]
    key = module_key_for_view(view)
    return [key] if key else []


def user_module_access(user, modulo):
    return EffectiveAccessService(user).module_access(modulo)


def can_delete_in_module(user, modulo):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user_module_access(user, modulo) == EDIT


def has_field_permission(user, campo, default_roles=None):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        perm = user.field_permissions.filter(campo=campo).only("pode_ver").first()
        if perm is not None:
            return bool(perm.pode_ver)
    except Exception:
        pass
    return has_role(user, default_roles or [])


class HasModuleRole(BasePermission):
    message = "Usuário sem permissão para acessar este módulo."

    def has_permission(self, request, view):
        return HasEffectiveModuleAccess().has_permission(request, view)


class HasEmpresaModulo(BasePermission):
    message = "Módulo não habilitado para a empresa do usuário."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        campo = getattr(view, "empresa_modulo_field", None)
        if not campo:
            return True

        empresa = getattr(user, "empresa", None)
        if not empresa:
            return False
        modulo = EMPRESA_FIELD_MODULE_MAP.get(campo)
        return EffectiveAccessService(user).module_access(modulo) != NONE


class HasActiveCompanyContract(BasePermission):
    message = "Contrato da empresa inválido."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return EffectiveAccessService(user).contract_state().active


class HasEffectiveModuleAccess(BasePermission):
    message = "Usuário sem permissão efetiva para acessar este módulo."

    def required_modules(self, view, request):
        modules = getattr(view, "required_modules", None)
        if modules:
            return [m for m in modules if m]
        module = getattr(view, "required_module", None) or module_key_for_view(view)
        return [module] if module else []

    def required_access(self, view, request):
        action_map = getattr(view, "action_required_access", {}) or {}
        action = getattr(view, "action", None)
        if action in action_map:
            return action_map[action]
        if request.method in SAFE_METHODS:
            return getattr(view, "read_access", VIEW)
        return getattr(view, "write_access", EDIT)

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        service = EffectiveAccessService(user)
        modules = self.required_modules(view, request)
        if not modules:
            return user.is_superuser
        return service.has_module_access(modules, self.required_access(view, request))


class CanManageCompanyUsers(BasePermission):
    message = "Usuário sem permissão para administrar usuários."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        service = EffectiveAccessService(user)
        return service.is_company_master() or service.has_module_access("configuracoes", EDIT)


class CanManageAccessProfiles(CanManageCompanyUsers):
    message = "Usuário sem permissão para administrar perfis."

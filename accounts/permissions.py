from rest_framework.permissions import BasePermission, SAFE_METHODS


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
    if not user or not user.is_authenticated or not modulo:
        return None
    try:
        perm = user.module_permissions.filter(modulo=modulo).only("acesso").first()
    except Exception:
        return None
    return perm.acesso if perm else None


def can_delete_in_module(user, modulo):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user_type(user) not in ADMIN_TYPES:
        return False
    acesso = user_module_access(user, modulo)
    return acesso in {None, "EDIT"}


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
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        if request.method in SAFE_METHODS:
            read_keys = module_keys_for_read(view)
            if read_keys:
                acessos = [user_module_access(request.user, key) for key in read_keys]
                acessos_definidos = [acesso for acesso in acessos if acesso is not None]
                if acessos_definidos:
                    if any(acesso in {"VIEW", "EDIT"} for acesso in acessos_definidos):
                        read_roles = getattr(view, "read_roles", None)
                        return has_role(request.user, read_roles) if read_roles is not None else True
                    return False

        modulo = module_key_for_view(view)
        acesso = user_module_access(request.user, modulo)
        if acesso is not None:
            if acesso == "NONE":
                return False
            if request.method in SAFE_METHODS:
                return acesso in {"VIEW", "EDIT"}
            if request.method == "DELETE":
                return can_delete_in_module(request.user, modulo)
            if acesso == "EDIT":
                return True
            action_roles = getattr(view, "action_roles", {}) or {}
            action = getattr(view, "action", None)
            if action in action_roles:
                return has_role(request.user, action_roles[action])
            return False

        action_roles = getattr(view, "action_roles", {}) or {}
        action = getattr(view, "action", None)
        if action in action_roles:
            return has_role(request.user, action_roles[action])

        read_roles = getattr(view, "read_roles", None)
        write_roles = getattr(view, "write_roles", None)
        roles = read_roles if request.method in SAFE_METHODS else write_roles
        if roles is None:
            roles = getattr(view, "allowed_roles", None)
        if request.method == "DELETE":
            return has_role(request.user, ADMIN_TYPES) and has_role(request.user, roles)
        return has_role(request.user, roles)


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
        return getattr(empresa, campo, False) is True

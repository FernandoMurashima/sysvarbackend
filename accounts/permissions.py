from rest_framework.permissions import BasePermission, SAFE_METHODS


ADMIN_TYPES = {"Admin", "Administrador"}


def user_type(user) -> str:
    return (getattr(user, "type", "") or "").strip()


def has_role(user, allowed_roles) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    current = user_type(user)
    return current in set(allowed_roles or [])


class HasModuleRole(BasePermission):
    message = "Usuário sem permissão para acessar este módulo."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_staff:
            return True

        action_roles = getattr(view, "action_roles", {}) or {}
        action = getattr(view, "action", None)
        if action in action_roles:
            return has_role(request.user, action_roles[action])

        read_roles = getattr(view, "read_roles", None)
        write_roles = getattr(view, "write_roles", None)
        roles = read_roles if request.method in SAFE_METHODS else write_roles
        if roles is None:
            roles = getattr(view, "allowed_roles", None)
        return has_role(request.user, roles)

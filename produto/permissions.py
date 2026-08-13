from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.services.effective_access import EDIT, EffectiveAccessService

class CanToggleProductFlags(BasePermission):
    """
    Permite GET para autenticados; para ações sensíveis (POST nas rotas customizadas),
    exige permissão funcional EDIT no módulo de produtos.
    """

    def has_permission(self, request, view):
        # Leitura para autenticados
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return EffectiveAccessService(user).has_module_access("produtos", EDIT)

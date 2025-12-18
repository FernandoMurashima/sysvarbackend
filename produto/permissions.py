from rest_framework.permissions import BasePermission, SAFE_METHODS

class CanToggleProductFlags(BasePermission):
    """
    Permite GET para autenticados; para ações sensíveis (POST nas rotas customizadas),
    exige usuário autenticado E (is_staff OU permissão 'change' do respectivo model).

    Observação: a View deve expor o atributo `model_perm_codename`, ex.:
      - 'produto.change_produto'
      - 'produto.change_produtodetalhe'
    """

    def has_permission(self, request, view):
        # Leitura para autenticados
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)

        # Para POST/ações customizadas: staff OU permissão de change no model informado
        model_perm = getattr(view, "model_perm_codename", None)
        if model_perm:
            return bool(
                request.user
                and request.user.is_authenticated
                and (request.user.is_staff or request.user.has_perm(model_perm))
            )

        # fallback: exige apenas autenticado
        return bool(request.user and request.user.is_authenticated)

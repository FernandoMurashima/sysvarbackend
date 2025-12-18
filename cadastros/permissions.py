from rest_framework.permissions import BasePermission

class HasCadastrosAccess(BasePermission):
    message = "Usuário sem permissão cadastros.access para usar os endpoints de Cadastros."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.has_perm("cadastros.access"))

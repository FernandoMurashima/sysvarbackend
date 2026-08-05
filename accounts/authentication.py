from rest_framework.authentication import TokenAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from accounts.services.sessions import ConcurrentSessionService
from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

PASSWORD_CHANGE_REQUIRED_CODE = "PASSWORD_CHANGE_REQUIRED"
PASSWORD_CHANGE_REQUIRED_MESSAGE = "Você precisa alterar sua senha antes de continuar."

PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/me/",
    "/api/accounts/users/me/",
    "/api/accounts/change-required-password/",
    "/api/accounts/auth/logout/",
    "/api/auth/logout/",
    "/api/accounts/sessoes/heartbeat/",
}


class CompanyTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != b"token":
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Cabeçalho de autenticação inválido.")
        key = auth[1].decode()
        user, token, sessao = ConcurrentSessionService.validate_session_token(key)
        if (
            getattr(user, "deve_trocar_senha", False)
            and not getattr(user, "is_superuser", False)
            and request.path not in PASSWORD_CHANGE_ALLOWED_PATHS
        ):
            try:
                AuditService.denied(
                    AuditAction.PASSWORD_CHANGE_REQUIRED_ACCESS_DENIED,
                    category=AuditCategory.SECURITY,
                    request=request,
                    user=user,
                    empresa=getattr(user, "empresa", None),
                    app_label="accounts",
                    model="user",
                    object_id=user.pk,
                    status_code=403,
                )
            except Exception:
                pass
            exc = PermissionDenied(PASSWORD_CHANGE_REQUIRED_MESSAGE)
            exc.detail = {"detail": PASSWORD_CHANGE_REQUIRED_MESSAGE, "code": PASSWORD_CHANGE_REQUIRED_CODE}
            raise exc
        user._current_access_session = sessao
        request.access_session = sessao
        return user, token

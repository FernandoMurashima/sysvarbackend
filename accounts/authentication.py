from rest_framework.authentication import TokenAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from accounts.services.sessions import ConcurrentSessionService


class CompanyTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != b"token":
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Cabeçalho de autenticação inválido.")
        key = auth[1].decode()
        user, token, sessao = ConcurrentSessionService.validate_session_token(key)
        user._current_access_session = sessao
        request.access_session = sessao
        return user, token

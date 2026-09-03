from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from fiscal.models import AgenteLocalSysvar


class AgentTokenAuthentication(BaseAuthentication):
    keyword = b"agent"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            return None
        if auth[0].lower() != self.keyword:
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Cabeçalho de autenticação do agente inválido.")
        try:
            token = auth[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuthenticationFailed("Token do agente inválido.") from exc
        token_hash = AgenteLocalSysvar.hash_token(token)
        agente = AgenteLocalSysvar.objects.select_related("empresa").filter(token_hash=token_hash, ativo=True).first()
        if not agente:
            raise AuthenticationFailed("Token do agente inválido.")
        request.agente_local_sysvar = agente
        return agente, token

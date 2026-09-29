from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from hub.models import SysvarHub


HUB_CREDENTIAL_INVALID_CODE = "HUB_CREDENTIAL_INVALID"
HUB_CREDENTIAL_INVALID_DETAIL = "Credencial do Hub inválida ou revogada."


class HubCredentialInvalid(AuthenticationFailed):
    default_detail = {
        "code": HUB_CREDENTIAL_INVALID_CODE,
        "detail": HUB_CREDENTIAL_INVALID_DETAIL,
    }
    default_code = "hub_credential_invalid"


class HubTokenAuthentication(BaseAuthentication):
    keyword = b"hub"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            return None
        if auth[0].lower() != self.keyword:
            return None
        if len(auth) != 2:
            raise HubCredentialInvalid()
        try:
            token = auth[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HubCredentialInvalid() from exc

        token_hash = SysvarHub.hash_token(token)
        hub = SysvarHub.objects.select_related("loja", "loja__empresa").filter(token_hash=token_hash, ativo=True).first()
        if not hub:
            raise HubCredentialInvalid()
        request.sysvar_hub = hub
        return hub, token

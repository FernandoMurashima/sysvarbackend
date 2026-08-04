from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.services.effective_access import EffectiveAccessService


class CompanyTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if user.is_superuser:
            return user, token
        state = EffectiveAccessService(user).contract_state()
        if not state.active:
            raise AuthenticationFailed(state.reason or "Acesso indisponível.")
        return user, token

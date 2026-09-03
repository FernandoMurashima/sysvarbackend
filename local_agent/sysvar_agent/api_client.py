import requests


class ApiError(Exception):
    transient = False


class TransientApiError(ApiError):
    transient = True


class NonRetryableApiError(ApiError):
    transient = False


class AuthApiError(NonRetryableApiError):
    pass


class SysvarApiClient:
    def __init__(self, base_url, token, timeout=15, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Agent {token}", "Accept": "application/json"})

    def get_configuracoes(self):
        return self._request("get", "/api/fiscal/agente-local/configuracoes/")

    def heartbeat(self, versao, hostname):
        return self._request("post", "/api/fiscal/agente-local/heartbeat/", json={"versao": versao, "hostname": hostname})

    def enviar_xml_detectado(self, payload):
        return self._request("post", "/api/fiscal/agente-local/xml-detectado/", json=payload)

    def _request(self, method, path, **kwargs):
        try:
            resp = getattr(self.session, method)(f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientApiError("Falha transitória de conexão com o Sysvar.") from exc
        if resp.status_code in (401, 403):
            raise AuthApiError("Autenticação do agente recusada pelo Sysvar.")
        if resp.status_code == 400:
            raise NonRetryableApiError(self._safe_error(resp, "Requisição inválida."))
        if resp.status_code >= 500:
            raise TransientApiError(f"Sysvar indisponível: HTTP {resp.status_code}.")
        if resp.status_code >= 400:
            raise NonRetryableApiError(f"Erro HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise TransientApiError("Resposta JSON inválida do Sysvar.") from exc

    def _safe_error(self, resp, fallback):
        try:
            data = resp.json()
        except ValueError:
            return fallback
        return str(data)[:500]


class SysvarActivationClient:
    def __init__(self, base_url, timeout=15, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def activate(self, payload):
        try:
            resp = self.session.post(f"{self.base_url}/api/fiscal/agente-local/ativar/", json=payload, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientApiError("Falha transitória de conexão com o Sysvar.") from exc
        if resp.status_code == 429:
            raise NonRetryableApiError("Muitas tentativas de ativação. Aguarde e tente novamente.")
        if resp.status_code == 400:
            raise NonRetryableApiError(self._safe_error(resp, "Código de ativação inválido ou expirado."))
        if resp.status_code >= 500:
            raise TransientApiError(f"Sysvar indisponível: HTTP {resp.status_code}.")
        if resp.status_code >= 400:
            raise NonRetryableApiError(f"Erro HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise TransientApiError("Resposta JSON inválida do Sysvar.") from exc
        if not isinstance(data, dict):
            raise NonRetryableApiError("Resposta de ativação inválida.")
        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise NonRetryableApiError("Resposta de ativação sem token.")
        return data

    def _safe_error(self, resp, fallback):
        try:
            data = resp.json()
        except ValueError:
            return fallback
        return str(data)[:500]

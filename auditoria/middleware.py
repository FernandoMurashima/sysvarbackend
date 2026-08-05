from .utils import clear_current_request, get_current_ip, get_current_request, get_current_user, set_current_request


class AuditContextMiddleware:
    """Cria contexto completo de auditoria por requisição."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            clear_current_request(token)
        request_id = getattr(request, "audit_request_id", None)
        correlation_id = getattr(request, "audit_correlation_id", None)
        if request_id:
            response["X-Request-ID"] = str(request_id)
        if correlation_id:
            response["X-Correlation-ID"] = str(correlation_id)
        return response

RequestMiddleware = AuditContextMiddleware

__all__ = ["AuditContextMiddleware", "RequestMiddleware", "get_current_request", "get_current_user", "get_current_ip"]

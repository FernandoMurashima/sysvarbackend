# auditoria/middleware.py
from .utils import set_current_request, get_current_request, get_current_user, get_current_ip

class RequestMiddleware:
    """Guarda o request em thread-local para ser usado pelos signals."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            # limpa ao final da requisição
            set_current_request(None)
        return response

# Re-exporta helpers (compatibilidade com imports existentes)
__all__ = ["RequestMiddleware", "get_current_request", "get_current_user", "get_current_ip"]

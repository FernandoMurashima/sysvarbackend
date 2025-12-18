# auditoria/apps.py
from django.apps import AppConfig

class AuditoriaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auditoria"

    def ready(self):
        # Conecta os signals
        from . import signals  # noqa: F401

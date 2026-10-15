from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured

from .backup_storage import raiz_backup_privada


@register(Tags.security)
def check_hub_backup_storage(app_configs, **kwargs):
    try:
        raiz_backup_privada()
    except (ImproperlyConfigured, TypeError, ValueError, OSError):
        return [Error(
            "Diretorio privado de backup Hub invalido.",
            hint="Configure HUB_BACKUP_ROOT fora do projeto, media, static e raizes publicas do Nginx (HUB_BACKUP_PUBLIC_ROOTS).",
            id="hub.E001",
        )]
    return []

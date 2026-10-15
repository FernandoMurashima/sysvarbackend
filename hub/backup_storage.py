"""Localizacao privada para uso futuro; nao cria diretorios nem abre arquivos."""
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError


def raiz_backup_privada():
    root = Path(settings.HUB_BACKUP_ROOT)
    if not root.is_absolute():
        raise ImproperlyConfigured("HUB_BACKUP_ROOT exige diretorio absoluto e privado.")
    root = root.resolve()
    public = [settings.BASE_DIR, settings.MEDIA_ROOT, settings.STATIC_ROOT]
    public.extend(value[1] if isinstance(value, (tuple, list)) else value for value in getattr(settings, "STATICFILES_DIRS", []))
    public.extend(getattr(settings, "HUB_BACKUP_PUBLIC_ROOTS", []))
    for value in public:
        if not value:
            continue
        path = Path(value).resolve()
        if root == path or root in path.parents or path in root.parents:
            raise ImproperlyConfigured("HUB_BACKUP_ROOT deve ficar fora do projeto e dos diretorios publicos.")
    return root


def caminho_backup_privado(backup):
    expected = backup.gerar_chave_armazenamento()
    if backup.chave_armazenamento != expected:
        raise ValidationError("Localizacao logica de backup divergente.")
    # Chave formada apenas por IDs/UUIDs da Central; nao usa nome/caminho do cliente.
    root = raiz_backup_privada()
    path = root.joinpath(*expected.split("/")).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValidationError("Localizacao de backup deve permanecer no diretorio privado.")
    return path

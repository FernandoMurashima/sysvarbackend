"""I/O privado, sem seguir links ou sobrescrever arquivos publicados."""
import contextlib
import ctypes
import os
import stat
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .backup_storage import raiz_backup_privada


class BackupIOError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def _windows_security():
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    token = w.HANDLE(); count = w.DWORD(); text = w.LPWSTR()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("Identidade Windows indisponivel.")
    try:
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(count))
        buf = ctypes.create_string_buffer(count.value)
        if not adv.GetTokenInformation(token, 1, buf, count.value, ctypes.byref(count)):
            raise OSError("Identidade Windows indisponivel.")
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise OSError("Identidade Windows indisponivel.")
        return adv, kernel, text.value
    finally:
        if text:
            kernel.LocalFree(text)
        kernel.CloseHandle(token)


def proteger_novo(path, directory=False):
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600, follow_symlinks=False)
        return
    adv, kernel, sid = _windows_security()
    descriptor = ctypes.c_void_p()
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;BA)(A;{inheritance};FA;;;{sid})"
    try:
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None) or not adv.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError("Permissoes privadas indisponiveis.")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)


def _root_permissions(root):
    if os.name != "nt":
        info = root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ImproperlyConfigured("Storage privado exige proprietario do processo e modo 0700.")
        return
    import re
    from ctypes import wintypes as w
    adv, kernel, sid = _windows_security()
    descriptor = ctypes.c_void_p(); text = w.LPWSTR()
    try:
        if adv.GetNamedSecurityInfoW(str(root), 1, 4, None, None, None, None, ctypes.byref(descriptor)) != 0 or not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None):
            raise OSError("DACL privada indisponivel.")
        sddl = text.value
        allowed = {"SY", "BA", sid}
        aliases = {"S-1-5-18": "SY", "S-1-5-19": "LS", "S-1-5-20": "NS"}
        if sid in aliases:
            allowed.add(aliases[sid])
        entries = re.findall(r"\(([^()]*)\)", sddl)
        if not entries or any(ace.split(";")[0] != "A" or ace.split(";")[-1] not in allowed for ace in entries):
            raise ImproperlyConfigured("Storage exige DACL exclusiva do servico, SYSTEM e Administradores.")
    finally:
        if text:
            kernel.LocalFree(text)
        if descriptor:
            kernel.LocalFree(descriptor)


def sem_links(path):
    path = Path(path)
    for node in [*reversed(path.parents), path]:
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BackupIOError("UNSAFE_STORAGE", 503)


def storage_root():
    configured = Path(settings.HUB_BACKUP_ROOT)
    sem_links(configured)
    root = raiz_backup_privada()
    if not root.is_dir():
        raise ImproperlyConfigured("Provisionar o diretorio privado antes de ativar recebimento.")
    _root_permissions(root)
    return root


def destination(backup):
    root = storage_root()
    # Nunca aceitar nomes/caminhos do manifesto ou do HTTP.
    if backup.chave_armazenamento != backup.gerar_chave_armazenamento():
        raise BackupIOError("UNSAFE_STORAGE")
    path = root.joinpath(*backup.chave_armazenamento.split("/"))
    if not path.is_relative_to(root) or ".." in path.parts:
        raise BackupIOError("UNSAFE_STORAGE")
    sem_links(path)
    return path


def ensure_parent(path):
    root = storage_root()
    if not path.is_relative_to(root):
        raise BackupIOError("UNSAFE_STORAGE")
    current = root
    for part in path.parent.relative_to(root).parts:
        current /= part
        sem_links(current)
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if not current.is_dir():
                raise BackupIOError("UNSAFE_STORAGE") from None
        else:
            proteger_novo(current, directory=True)
        sem_links(current)


def stage_path(upload):
    final = destination(upload.backup)
    return final.with_name(".upload-" + upload.upload_uuid.hex + ".partial")


@contextlib.contextmanager
def open_private(path, *, create=False, writable=False):
    sem_links(path)
    flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        actual = os.fstat(fd); named = path.lstat()
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino) or (writable and actual.st_nlink != 1):
            raise BackupIOError("UNSAFE_STORAGE")
        if create:
            proteger_novo(path)
        with os.fdopen(fd, "r+b" if writable else "rb") as stream:
            fd = None
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


@contextlib.contextmanager
def backup_lock(backup, *, existing_only=False):
    final = destination(backup)
    path = final.with_name(".backup-" + backup.backup_uuid.hex + ".lock")
    if not existing_only:
        ensure_parent(final)
        try:
            with open_private(path, create=True, writable=True) as stream:
                stream.write(b"\x00"); stream.flush()
        except FileExistsError:
            pass
    with open_private(path, writable=True) as stream:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BackupIOError("UPLOAD_BUSY", 409) from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            # Fechamento do handle libera flock e tambem protege em crash do processo.


def publish(upload):
    stage, final = stage_path(upload), destination(upload.backup)
    sem_links(stage); sem_links(final)
    # Hardlink O_EXCL no mesmo filesystem: publica atomicamente sem sobrescrever.
    with open_private(stage, writable=True) as stream:
        os.link(stage, final, follow_symlinks=False)
        os.fsync(stream.fileno())
    if os.name != "nt":
        fd = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    # Somente staging desta tentativa, e apenas depois da publicacao confirmada.
    stage.unlink()

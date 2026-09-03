import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from .config import default_config_path
from .host import run_agent


SERVICE_NAME = "SysvarLocalAgent"
SERVICE_DISPLAY_NAME = "Sysvar Local Agent"
SERVICE_DESCRIPTION = "Serviço local de integração do Sysvar para detecção segura de XML de NF-e."

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    import pywintypes
    import pythoncom
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None
    pywintypes = None
    pythoncom = None


log = logging.getLogger(__name__)


def service_config_path():
    configured = os.environ.get("SYSVAR_AGENT_CONFIG")
    if configured:
        return str(Path(configured).resolve())
    return str(default_config_path())


if win32serviceutil is not None:
    class SysvarLocalAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            super().__init__(args)
            self.stop_event = threading.Event()
            self.stop_handle = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            win32event.SetEvent(self.stop_handle)

        def SvcDoRun(self):
            try:
                servicemanager.LogInfoMsg(f"{SERVICE_DISPLAY_NAME} iniciando.")
                run_agent(config_path=service_config_path(), once=False, stop_event=self.stop_event)
                servicemanager.LogInfoMsg(f"{SERVICE_DISPLAY_NAME} encerrado.")
            except Exception as exc:
                servicemanager.LogErrorMsg(f"{SERVICE_DISPLAY_NAME} falhou: {exc}")
                raise
else:
    class SysvarLocalAgentService:
        pass


def main():
    if win32serviceutil is None:
        raise RuntimeError("pywin32 não está instalado. Instale as dependências do local_agent antes de registrar o serviço.")
    _ensure_default_startup_auto(sys.argv)
    if _command_requires_runtime_prepare(sys.argv):
        prepare_windows_service_runtime()
    win32serviceutil.HandleCommandLine(SysvarLocalAgentService)


def _ensure_default_startup_auto(argv):
    if "--startup" in argv:
        return argv
    install_index = next((idx for idx, arg in enumerate(argv[1:], start=1) if arg.lower() == "install"), None)
    if install_index is None:
        return argv
    argv[install_index:install_index] = ["--startup", "auto"]
    return argv


def _command_requires_runtime_prepare(argv):
    return any(arg.lower() in {"install", "update"} for arg in argv[1:])


def prepare_windows_service_runtime():
    host = locate_pythonservice_exe()
    files = [locate_python_runtime_dll()]
    files.extend(locate_python_support_dlls())
    files.extend(locate_pywin32_runtime_dlls())
    host_dir = host.parent
    for src in files:
        dst = host_dir / src.name
        if not dst.exists() or src.stat().st_size != dst.stat().st_size:
            shutil.copy2(src, dst)
    return {"host": host, "files": files}


def locate_pythonservice_exe():
    candidates = [
        Path(sys.prefix) / "pythonservice.exe",
        Path(sys.prefix) / "Scripts" / "pythonservice.exe",
    ]
    which = shutil.which("pythonservice.exe")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise RuntimeError("pythonservice.exe não encontrado no ambiente do agente.")


def locate_python_runtime_dll():
    version = f"{sys.version_info.major}{sys.version_info.minor}"
    names = [f"python{version}.dll", f"python{sys.version_info.major}.dll"]
    roots = [Path(sys.base_prefix), Path(sys.prefix), Path(sys.executable).resolve().parent]
    for name in names:
        for root in roots:
            candidate = root / name
            if candidate.exists():
                return candidate.resolve()
    raise RuntimeError(f"DLL principal do Python não encontrada: python{version}.dll.")


def locate_python_support_dlls():
    files = []
    for root in (Path(sys.base_prefix), Path(sys.prefix), Path(sys.executable).resolve().parent):
        candidate = root / f"python{sys.version_info.major}.dll"
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved not in files:
                files.append(resolved)
    return files


def locate_pywin32_runtime_dlls():
    files = []
    for module in (pywintypes, pythoncom):
        path = Path(getattr(module, "__file__", "") or "")
        if path.name.lower().endswith(".dll") and path.exists():
            files.append(path.resolve())
    return files


if __name__ == "__main__":
    main()

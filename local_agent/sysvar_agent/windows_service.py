import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
import tempfile
from pathlib import Path

from .config import DEFAULT_CONFIG, ConfigError, default_config_path
from .config import load_config
from .host import run_agent


SERVICE_NAME = "SysvarLocalAgent"
SERVICE_DISPLAY_NAME = "Sysvar Local Agent"
SERVICE_DESCRIPTION = "Serviço local de integração do Sysvar para detecção segura de XML de NF-e."
PYTHON_MODULE = "sysvar_agent.windows_service"
PYTHON_CLASS = f"{PYTHON_MODULE}.SysvarLocalAgentService"
CONFIG_OPTION = "ConfigPath"
PLACEHOLDER_TOKEN = "COLOQUE_O_TOKEN_AQUI"

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


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def configure_service_host():
    if is_frozen():
        SysvarLocalAgentService._exe_name_ = sys.executable
        SysvarLocalAgentService._exe_args_ = ""
        return {"host": Path(sys.executable).resolve(), "mode": "frozen"}
    SysvarLocalAgentService._exe_name_ = None
    SysvarLocalAgentService._exe_args_ = None
    runtime = prepare_windows_service_runtime()
    configure_pythonservice_path(runtime["host"])
    validate_service_host_imports()
    return {"host": runtime["host"], "mode": "python"}


def run_frozen_service():
    if servicemanager is None:
        raise RuntimeError("pywin32 não está disponível no executável do serviço.")
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(SysvarLocalAgentService)
    servicemanager.StartServiceCtrlDispatcher()


def service_config_path():
    persisted = persisted_service_config_path()
    if persisted:
        return persisted
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

SysvarLocalAgentService.__module__ = PYTHON_MODULE
sys.modules.setdefault(PYTHON_MODULE, sys.modules[__name__])


def main():
    if win32serviceutil is None:
        raise RuntimeError("pywin32 não está instalado. Instale as dependências do local_agent antes de registrar o serviço.")
    if is_frozen() and len(sys.argv) == 1:
        run_frozen_service()
        return
    command = service_command(sys.argv)
    if command == "config-ok":
        raise SystemExit(0 if config_allows_service_start() else 1)
    _ensure_default_startup_auto(sys.argv)
    config_path = None
    if command in {"install", "update"}:
        config_path = install_config_path(command)
    if _command_requires_runtime_prepare(sys.argv):
        configure_service_host()
    handler = config_option_handler(config_path) if config_path else None
    win32serviceutil.HandleCommandLine(SysvarLocalAgentService, customOptionHandler=handler)


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


def service_command(argv):
    commands = {"install", "update", "start", "stop", "restart", "remove", "debug", "config-ok"}
    return next((arg.lower() for arg in argv[1:] if arg.lower() in commands), None)


def config_option_handler(path):
    def handler(_opts):
        persist_service_config_path(path)

    return handler


def config_allows_service_start(path=None):
    try:
        cfg = load_config(path or service_config_path())
    except Exception:
        return False
    return bool(cfg.api_base_url and cfg.token and cfg.token != PLACEHOLDER_TOKEN)


def persisted_service_config_path():
    if win32serviceutil is None:
        return None
    try:
        configured = win32serviceutil.GetServiceCustomOption(SERVICE_NAME, CONFIG_OPTION, None)
    except Exception:
        return None
    if configured:
        return str(Path(configured).resolve())
    return None


def persist_service_config_path(path):
    if win32serviceutil is None:
        raise RuntimeError("pywin32 não está instalado. Não foi possível gravar o caminho do config.json do serviço.")
    resolved = str(Path(path).resolve())
    win32serviceutil.SetServiceCustomOption(SERVICE_NAME, CONFIG_OPTION, resolved)
    return resolved


def install_config_path(command):
    if command == "update":
        persisted = persisted_service_config_path()
        if persisted and Path(persisted).exists():
            return persisted
    candidate = os.environ.get("SYSVAR_AGENT_CONFIG")
    path = Path(candidate).expanduser() if candidate else development_config_path()
    path = path.resolve()
    if not path.exists():
        raise ConfigError(f"Arquivo de configuração do serviço não encontrado: {path}")
    return str(path)


def development_config_path():
    cwd_config = Path.cwd() / DEFAULT_CONFIG
    if cwd_config.exists():
        return cwd_config
    venv_parent_config = Path(sys.prefix).resolve().parent / DEFAULT_CONFIG
    if venv_parent_config.exists():
        return venv_parent_config
    source_tree_config = Path(sys.prefix).resolve().parent / "local_agent" / DEFAULT_CONFIG
    if source_tree_config.exists():
        return source_tree_config
    return default_config_path()


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


def ensure_package_importable_for_service_host():
    return validate_service_host_imports()


def configure_pythonservice_path(host=None):
    host = host or locate_pythonservice_exe()
    paths = service_python_paths()
    write_pythonservice_pth(host, paths)
    return paths


def service_python_paths():
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    stdlib = Path(sysconfig.get_path("stdlib") or Path(sys.base_prefix) / "Lib")
    dlls = Path(sys.base_prefix) / "DLLs"
    paths = [
        stdlib,
        dlls,
        site_packages,
        site_packages / "win32",
        site_packages / "win32" / "lib",
        site_packages / "pythonwin",
    ]
    return [path.resolve() for path in paths if path.exists()]


def write_pythonservice_pth(host, paths):
    pth = host.with_name("pythonservice._pth")
    lines = [str(path) for path in paths]
    lines.append("import site")
    content = "\n".join(lines) + "\n"
    if not pth.exists() or pth.read_text(encoding="utf-8") != content:
        pth.write_text(content, encoding="utf-8")
    return pth


def validate_service_host_imports():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    paths_literal = repr([str(path) for path in service_python_paths()])
    modules = [
        "servicemanager",
        "win32service",
        "win32serviceutil",
        "win32event",
        "pywintypes",
        "pythoncom",
        PYTHON_MODULE,
    ]
    modules_literal = repr(modules)
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib, sys; "
                    f"sys.path[:] = {paths_literal}; "
                    f"[importlib.import_module(name) for name in {modules_literal}]; "
                    f"mod = importlib.import_module('{PYTHON_MODULE}'); "
                    f"assert getattr(mod, 'PYTHON_CLASS') == '{PYTHON_CLASS}'; "
                    f"assert '\\\\' not in '{PYTHON_CLASS}'"
                ),
            ],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(
            "Ambiente Python do serviço não consegue importar sysvar_agent/pywin32. "
            "Execute no diretório local_agent: python -m pip install . "
            f"Detalhe: {detail}"
        )
    return True


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

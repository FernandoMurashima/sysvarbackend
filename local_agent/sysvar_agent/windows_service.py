import logging
import os
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
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


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
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "install" and "--startup" not in sys.argv:
        sys.argv.extend(["--startup", "auto"])
    win32serviceutil.HandleCommandLine(SysvarLocalAgentService)


if __name__ == "__main__":
    main()

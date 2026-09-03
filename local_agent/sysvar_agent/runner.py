import json
import logging
import socket
import time

from . import __version__
from .api_client import AuthApiError, NonRetryableApiError, TransientApiError
from .scanner import DirectoryScanner


log = logging.getLogger(__name__)


class AgentRunner:
    def __init__(self, config, api_client, queue):
        self.config = config
        self.api = api_client
        self.queue = queue
        self.scanner = DirectoryScanner(queue, config.min_file_age_seconds)
        self.hostname = socket.gethostname()
        self.configuracoes = []
        self.last_config_refresh = 0
        self.last_heartbeat = 0

    def run_once(self):
        self.refresh_configuracoes()
        self.send_heartbeat()
        self.scanner.scan(self.configuracoes)
        self.flush_queue()

    def run_forever(self, stop_event=None):
        while not _should_stop(stop_event):
            self.run_once()
            if stop_event is not None:
                stop_event.wait(self.config.poll_interval_seconds)
            else:
                time.sleep(self.config.poll_interval_seconds)

    def refresh_configuracoes(self):
        now = time.time()
        if self.configuracoes and now - self.last_config_refresh < self.config.heartbeat_interval_seconds:
            return
        try:
            data = self.api.get_configuracoes()
            self.configuracoes = list(data.get("configuracoes") or [])
            self.last_config_refresh = now
            log.info("Configurações carregadas: %s", len(self.configuracoes))
        except Exception as exc:
            log.error("Falha ao buscar configurações: %s", exc)

    def send_heartbeat(self):
        now = time.time()
        if now - self.last_heartbeat < self.config.heartbeat_interval_seconds:
            return
        try:
            self.api.heartbeat(__version__, self.hostname)
            self.last_heartbeat = now
            log.info("Heartbeat enviado.")
        except Exception as exc:
            log.error("Falha no heartbeat: %s", exc)

    def flush_queue(self):
        for item in self.queue.due_items():
            payload = json.loads(item["payload_json"])
            try:
                resp = self.api.enviar_xml_detectado(payload)
                if resp.get("created") in (True, False):
                    self.queue.mark_sent(item["id"])
                    log.info("XML aceito: %s", _mask(payload.get("chave_acesso")))
                else:
                    self.queue.mark_error(item["id"], "Resposta inesperada do backend.", retry=True)
            except AuthApiError as exc:
                self.queue.mark_error(item["id"], exc, retry=True)
                log.error("Autenticação recusada ao enviar XML: %s", exc)
            except TransientApiError as exc:
                self.queue.mark_error(item["id"], exc, retry=True)
                log.warning("Falha transitória ao enviar XML: %s", exc)
            except NonRetryableApiError as exc:
                self.queue.mark_error(item["id"], exc, retry=False)
                log.error("XML recusado sem retry: %s", exc)


def _mask(chave):
    chave = str(chave or "")
    return f"{chave[:6]}...{chave[-4:]}" if len(chave) > 10 else "***"


def _should_stop(stop_event):
    return bool(stop_event is not None and stop_event.is_set())

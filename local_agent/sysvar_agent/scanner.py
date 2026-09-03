import logging
import time
from pathlib import Path

from .nfe_parser import NFeParseError, parse_nfe_file


log = logging.getLogger(__name__)


class DirectoryScanner:
    def __init__(self, queue, min_file_age_seconds=3):
        self.queue = queue
        self.min_file_age_seconds = min_file_age_seconds

    def scan(self, configuracoes):
        queued = 0
        for cfg in configuracoes:
            path = Path(cfg.get("caminho_local") or "")
            if not path.exists() or not path.is_dir():
                log.warning("Pasta não encontrada para configuração %s: %s", cfg.get("id"), path)
                continue
            for xml_path in path.glob("*.xml"):
                queued += 1 if self._handle_file(cfg, xml_path) else 0
        return queued

    def _handle_file(self, cfg, xml_path):
        try:
            stat = xml_path.stat()
            if time.time() - stat.st_mtime < self.min_file_age_seconds:
                return False
            payload = parse_nfe_file(xml_path)
        except (OSError, NFeParseError, ValueError) as exc:
            log.warning("XML ignorado em %s: %s", xml_path, exc)
            return False
        if self.queue.is_sent(payload["chave_acesso"]):
            return False
        payload["configuracao_id"] = cfg["id"]
        payload["caminho_origem_local"] = str(xml_path)
        return self.queue.enqueue(cfg["id"], str(xml_path), payload["chave_acesso"], stat.st_size, stat.st_mtime, payload)

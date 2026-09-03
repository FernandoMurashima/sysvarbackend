import logging

from .api_client import SysvarApiClient
from .config import load_config
from .logging_config import configure_logging
from .queue import AgentQueue
from .runner import AgentRunner


log = logging.getLogger(__name__)


def run_agent(config_path="config.json", once=False, stop_event=None):
    config = load_config(config_path)
    configure_logging(config.log_file)
    queue = AgentQueue(config.database_path)
    client = SysvarApiClient(config.api_base_url, config.token, config.request_timeout_seconds)
    runner = AgentRunner(config, client, queue)
    try:
        log.info("Sysvar Local Agent iniciando.")
        if once:
            runner.run_once()
        else:
            runner.run_forever(stop_event=stop_event)
        log.info("Sysvar Local Agent encerrado.")
    finally:
        queue.close()

import argparse

from .api_client import SysvarApiClient
from .config import load_config
from .logging_config import configure_logging
from .queue import AgentQueue
from .runner import AgentRunner


def main():
    parser = argparse.ArgumentParser(prog="sysvar_agent")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(config.log_file)
    queue = AgentQueue(config.database_path)
    client = SysvarApiClient(config.api_base_url, config.token, config.request_timeout_seconds)
    runner = AgentRunner(config, client, queue)
    try:
        if args.once:
            runner.run_once()
        else:
            runner.run_forever()
    finally:
        queue.close()


if __name__ == "__main__":
    main()

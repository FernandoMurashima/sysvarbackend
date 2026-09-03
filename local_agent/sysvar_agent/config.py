import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = "config.json"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    api_base_url: str
    token: str
    poll_interval_seconds: int = 15
    heartbeat_interval_seconds: int = 60
    request_timeout_seconds: int = 15
    min_file_age_seconds: int = 3
    database_path: str = "data/agent.db"
    log_file: str = "logs/sysvar-agent.log"

    def __repr__(self):
        return (
            "AgentConfig(api_base_url={!r}, token='***', poll_interval_seconds={!r}, "
            "heartbeat_interval_seconds={!r}, request_timeout_seconds={!r})"
        ).format(
            self.api_base_url,
            self.poll_interval_seconds,
            self.heartbeat_interval_seconds,
            self.request_timeout_seconds,
        )


def load_config(path=DEFAULT_CONFIG) -> AgentConfig:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    data = {}
    if not config_path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    token = os.environ.get("SYSVAR_AGENT_TOKEN") or data.get("token")
    cfg = AgentConfig(
        api_base_url=str(data.get("api_base_url") or "").rstrip("/"),
        token=str(token or ""),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 15)),
        heartbeat_interval_seconds=int(data.get("heartbeat_interval_seconds", 60)),
        request_timeout_seconds=int(data.get("request_timeout_seconds", 15)),
        min_file_age_seconds=int(data.get("min_file_age_seconds", 3)),
        database_path=str(_resolve_local_path(data.get("database_path") or "data/agent.db", config_path)),
        log_file=str(_resolve_local_path(data.get("log_file") or "logs/sysvar-agent.log", config_path)),
    )
    validate_config(cfg)
    return cfg


def validate_config(config: AgentConfig):
    if not config.api_base_url:
        raise ConfigError("api_base_url é obrigatório.")
    if not config.token:
        raise ConfigError("token é obrigatório.")
    for field in ("poll_interval_seconds", "heartbeat_interval_seconds", "request_timeout_seconds", "min_file_age_seconds"):
        if int(getattr(config, field)) <= 0:
            raise ConfigError(f"{field} deve ser positivo.")


def default_config_path():
    return (Path.cwd() / DEFAULT_CONFIG).resolve()


def _resolve_local_path(value, config_path):
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()

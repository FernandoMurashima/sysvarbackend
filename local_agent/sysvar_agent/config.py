import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = "config.json"
PLACEHOLDER_TOKEN = "COLOQUE_O_TOKEN_AQUI"


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


def load_activation_config(path=DEFAULT_CONFIG):
    config_path = _resolve_config_path(path)
    data = _read_config_dict(config_path)
    api_base_url = str(data.get("api_base_url") or "").rstrip("/")
    if not api_base_url:
        raise ConfigError("api_base_url é obrigatório.")
    timeout = int(data.get("request_timeout_seconds", 15))
    if timeout <= 0:
        raise ConfigError("request_timeout_seconds deve ser positivo.")
    return config_path, data, api_base_url, timeout


def ensure_local_identifier(data):
    identificador = str(data.get("identificador") or "").strip()
    if identificador:
        return identificador, False
    import secrets

    identificador = f"SYSVAR-{secrets.token_hex(8).upper()}"
    data["identificador"] = identificador
    return identificador, True


def save_config_atomic(path, data):
    config_path = _resolve_config_path(path)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=config_path.parent, delete=False, prefix=f".{config_path.name}.", suffix=".tmp") as fh:
            tmp_name = fh.name
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, config_path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


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


def _resolve_config_path(path=DEFAULT_CONFIG):
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    return config_path


def _read_config_dict(config_path):
    if not config_path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON de configuração inválido: {config_path}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Configuração deve ser um objeto JSON.")
    return data

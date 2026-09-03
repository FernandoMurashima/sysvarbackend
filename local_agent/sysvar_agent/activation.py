import getpass
import os
import socket
import sys

from . import __version__
from .api_client import SysvarActivationClient
from .config import ConfigError, ensure_local_identifier, load_activation_config, save_config_atomic


ACTIVATION_CODE_ENV = "SYSVAR_AGENT_ACTIVATION_CODE"


class ActivationError(RuntimeError):
    pass


def activation_code_from_env_or_prompt():
    code = os.environ.get(ACTIVATION_CODE_ENV)
    if code is None:
        code = getpass.getpass("Código de ativação: ")
    code = str(code or "").strip()
    if not code:
        raise ActivationError("Código de ativação não informado.")
    return code


def activate_agent(config_path, code=None, session=None, stdout=None):
    stdout = stdout or sys.stdout
    try:
        resolved_path, data, api_base_url, timeout = load_activation_config(config_path)
    except ConfigError:
        raise
    code = str(code or activation_code_from_env_or_prompt()).strip()
    if not code:
        raise ActivationError("Código de ativação não informado.")

    identificador, _created = ensure_local_identifier(data)
    hostname = socket.gethostname()
    payload = {
        "codigo": code,
        "identificador": identificador,
        "nome": f"Sysvar Local Agent - {hostname}",
        "hostname": hostname,
        "versao": __version__,
    }
    client = SysvarActivationClient(api_base_url, timeout=timeout, session=session)
    response = client.activate(payload)
    token = response["token"].strip()

    data["identificador"] = identificador
    data["token"] = token
    save_config_atomic(resolved_path, data)
    print("Local Agent ativado com sucesso.", file=stdout)
    print(f"Identificador: {identificador}", file=stdout)
    print(f"Hostname: {hostname}", file=stdout)
    return {"identificador": identificador, "hostname": hostname}

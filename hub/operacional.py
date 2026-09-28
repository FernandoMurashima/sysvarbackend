from rest_framework.exceptions import ValidationError


MAX_TERMINAIS_SNAPSHOT = 100
MAX_TEXTO = 180


def normalizar_snapshot_operacional(snapshot):
    if not isinstance(snapshot, dict):
        raise ValidationError("Snapshot operacional inválido.")

    terminais = snapshot.get("terminais", [])
    if terminais is None:
        terminais = []
    if not isinstance(terminais, list):
        raise ValidationError("Lista de terminais inválida.")

    return {
        "gerado_em": _texto(snapshot.get("gerado_em"), 40),
        "terminais": [_normalizar_terminal(item) for item in terminais[:MAX_TERMINAIS_SNAPSHOT] if isinstance(item, dict)],
    }


def _normalizar_terminal(item):
    return {
        "terminal_uuid": _texto(item.get("terminal_uuid"), 64),
        "codigo": _texto(item.get("codigo"), 30),
        "nome": _texto(item.get("nome"), 100),
        "ativo": bool(item.get("ativo")),
        "pareado": bool(item.get("pareado")),
        "pareado_em": _texto(item.get("pareado_em"), 40),
        "hostname": _texto(item.get("hostname"), 150),
        "ultimo_ip": _texto(item.get("ultimo_ip"), 45),
        "ultima_conexao_em": _texto(item.get("ultima_conexao_em"), 40),
        "online": bool(item.get("online")),
        "caixa": _normalizar_caixa(item.get("caixa")),
        "caixa_status": _status(item.get("caixa_status")),
        "sessao_caixa": _normalizar_sessao_caixa(item.get("sessao_caixa")),
    }


def _normalizar_caixa(caixa):
    if not isinstance(caixa, dict):
        return None
    return {
        "id": _inteiro(caixa.get("id")),
        "codigo": _texto(caixa.get("codigo"), 30),
        "descricao": _texto(caixa.get("descricao"), 150),
        "ativo": bool(caixa.get("ativo")),
    }


def _normalizar_sessao_caixa(sessao):
    if not isinstance(sessao, dict):
        return None
    return {
        "uuid": _texto(sessao.get("uuid"), 64),
        "status": _status(sessao.get("status")),
        "aberto_em": _texto(sessao.get("aberto_em"), 40),
        "operador": _normalizar_identificacao(sessao.get("operador")),
        "terminal_abertura": _normalizar_identificacao(sessao.get("terminal_abertura")),
    }


def _normalizar_identificacao(item):
    if not isinstance(item, dict):
        return None
    return {
        "codigo": _texto(item.get("codigo"), 30),
        "nome": _texto(item.get("nome"), 150),
    }


def _texto(valor, limite=MAX_TEXTO):
    if valor is None:
        return None
    return str(valor).strip()[:limite]


def _inteiro(valor):
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _status(valor):
    return _texto(valor, 30) or ""

import re
from decimal import Decimal

from django.utils.encoding import force_str


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "password", "senha", "token", "authorization", "cookie", "secret",
    "client_secret", "private_key", "certificate", "certificado",
    "refresh_token", "access_token", "session_token", "token_key_hash",
    "key_hash",
}

MAX_DEPTH = 5
MAX_LIST_ITEMS = 50
MAX_DICT_KEYS = 80
MAX_STRING = 2000


def _is_sensitive_key(key):
    normalized = force_str(key or "").lower()
    return any(part in normalized for part in SENSITIVE_KEYS)


def _mask_document(value):
    text = force_str(value)
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11:
        return f"***.{digits[3:6]}.{digits[6:9]}-**"
    if len(digits) == 14:
        return f"**.{digits[2:5]}.{digits[5:8]}/****-{digits[-2:]}"
    return text


def _mask_email(value):
    text = force_str(value)
    if "@" not in text:
        return text
    name, domain = text.split("@", 1)
    return f"{name[:2]}***@{domain}"


def _truncate_string(value, max_string=MAX_STRING):
    text = force_str(value)
    if len(text) <= max_string:
        return text
    return {"value": text[:max_string], "_truncated": True}


def sanitize_audit_data(value, *, depth=0, max_depth=MAX_DEPTH):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if depth >= max_depth:
        return {"_truncated": True}
    if isinstance(value, dict):
        output = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DICT_KEYS:
                output["_truncated"] = True
                break
            key_text = force_str(key)
            lower = key_text.lower()
            if _is_sensitive_key(key_text):
                output[key_text] = REDACTED
            elif lower in {"cpf", "cnpj", "documento", "inscricao_estadual"}:
                output[key_text] = _mask_document(item)
            elif lower in {"email", "e-mail"}:
                output[key_text] = _mask_email(item)
            elif "telefone" in lower or lower in {"celular", "fone"}:
                output[key_text] = _truncate_string(force_str(item)[:4] + "***")
            else:
                output[key_text] = sanitize_audit_data(item, depth=depth + 1, max_depth=max_depth)
        return output
    if isinstance(value, (list, tuple, set)):
        output = [sanitize_audit_data(item, depth=depth + 1, max_depth=max_depth) for item in list(value)[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            output.append({"_truncated": True})
        return output
    return _truncate_string(value)


def truncate_text(value, limit):
    if value in (None, ""):
        return None if value is None else ""
    text = force_str(value)
    return text[:limit]

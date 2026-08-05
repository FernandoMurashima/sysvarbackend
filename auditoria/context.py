import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


_audit_context = ContextVar("audit_context", default=None)


@dataclass
class AuditContext:
    request_id: uuid.UUID
    correlation_id: uuid.UUID | None = None
    request: Any = None
    user: Any = None
    empresa: Any = None
    loja: Any = None
    session: Any = None
    device_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    http_method: str | None = None
    endpoint: str | None = None


def get_audit_context():
    return _audit_context.get()


def set_audit_context(context):
    return _audit_context.set(context)


def reset_audit_context(token):
    _audit_context.reset(token)


def parse_uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None

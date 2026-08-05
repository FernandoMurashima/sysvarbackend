import logging

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.forms.models import model_to_dict

from .context import get_audit_context, parse_uuid
from .display import empresa_display_name, loja_display_name, user_display_name
from .models import AuditAction, AuditCategory, AuditLog, AuditOrigin, AuditResult, AuditSeverity
from .sanitizer import sanitize_audit_data, truncate_text

logger = logging.getLogger(__name__)


class AuditService:
    failure_count = 0

    @classmethod
    def success(cls, action, **kwargs):
        return cls.record(action=action, result=AuditResult.SUCCESS, severity=kwargs.pop("severity", AuditSeverity.INFO), **kwargs)

    @classmethod
    def failure(cls, action, **kwargs):
        return cls.record(action=action, result=AuditResult.FAILURE, severity=kwargs.pop("severity", AuditSeverity.ERROR), **kwargs)

    @classmethod
    def denied(cls, action, **kwargs):
        return cls.record(action=action, result=AuditResult.DENIED, severity=kwargs.pop("severity", AuditSeverity.WARNING), **kwargs)

    @classmethod
    def security(cls, action, **kwargs):
        return cls.record(action=action, category=AuditCategory.SECURITY, origin=kwargs.pop("origin", AuditOrigin.API), **kwargs)

    @classmethod
    def on_commit(cls, **kwargs):
        transaction.on_commit(lambda: cls._record_on_commit(kwargs))

    @classmethod
    def required(cls, **kwargs):
        kwargs["audit_required"] = True
        return cls.record(**kwargs)

    @classmethod
    def required_success(cls, action, **kwargs):
        return cls.required(action=action, result=AuditResult.SUCCESS, severity=kwargs.pop("severity", AuditSeverity.INFO), **kwargs)

    @classmethod
    def _record_on_commit(cls, kwargs):
        try:
            return cls.record(**kwargs)
        except Exception:
            logger.exception("Falha em callback on_commit de auditoria.")
            return None

    @classmethod
    def record(
        cls,
        *,
        action,
        category=AuditCategory.SYSTEM,
        result=AuditResult.SUCCESS,
        severity=AuditSeverity.INFO,
        origin=AuditOrigin.API,
        instance=None,
        empresa=None,
        loja=None,
        user=None,
        request=None,
        session=None,
        before=None,
        after=None,
        changed_fields=None,
        metadata=None,
        error_code=None,
        error_message=None,
        status_code=None,
        correlation_id=None,
        audit_required=False,
        app_label=None,
        model=None,
        object_id=None,
        object_repr=None,
        changes=None,
    ):
        try:
            if AuditLog._meta.db_table not in connection.introspection.table_names():
                return None
            action = cls._normalize_action(action)
            category = cls._choice(category, AuditCategory)
            result = cls._choice(result, AuditResult)
            severity = cls._choice(severity, AuditSeverity)
            origin = cls._choice(origin, AuditOrigin)
            ctx = get_audit_context()
            request = request or getattr(ctx, "request", None)
            user = cls._resolve_user(user, request, ctx)
            session = session or getattr(ctx, "session", None) or getattr(request, "access_session", None)
            instance_empresa = cls._extract_empresa(instance)
            instance_loja = cls._extract_loja(instance)
            empresa = empresa or instance_empresa or getattr(user, "empresa", None) or getattr(session, "empresa", None)
            loja = loja or instance_loja or getattr(session, "loja", None) or getattr(user, "loja", None)
            if loja is not None and empresa is None:
                empresa = getattr(loja, "empresa", None)
            app_label, model, object_id, object_repr = cls._object_context(instance, app_label, model, object_id, object_repr)
            request_id = getattr(ctx, "request_id", None)
            if not request_id and request is not None:
                request_id = getattr(request, "audit_request_id", None)
            payload = {
                "request_id": request_id,
                "correlation_id": parse_uuid(correlation_id) or getattr(ctx, "correlation_id", None),
                "empresa": empresa if getattr(empresa, "pk", None) else None,
                "empresa_id_snapshot": truncate_text(getattr(empresa, "pk", None), 64),
                "empresa_nome_snapshot": truncate_text(empresa_display_name(empresa), 160),
                "loja": loja if getattr(loja, "pk", None) else None,
                "loja_id_snapshot": truncate_text(getattr(loja, "pk", None), 64),
                "loja_nome_snapshot": truncate_text(loja_display_name(loja), 120),
                "user": user if getattr(user, "is_authenticated", False) else None,
                "user_id_snapshot": truncate_text(getattr(user, "pk", None), 64),
                "username_snapshot": truncate_text(getattr(user, "username", None), 150),
                "user_nome_snapshot": truncate_text(user_display_name(user), 180),
                "session_id": getattr(session, "session_id", None) or getattr(ctx, "session_id", None),
                "device_id": truncate_text(getattr(session, "dispositivo_id", None) or getattr(ctx, "device_id", None), 128),
                "action": truncate_text(action, 64),
                "category": category,
                "result": result,
                "severity": severity,
                "origin": origin,
                "app_label": truncate_text(app_label or "system", 50),
                "model": truncate_text(model or "event", 100),
                "object_id": truncate_text(object_id, 64),
                "object_repr": truncate_text(object_repr, 255),
                "before_data": sanitize_audit_data(before),
                "after_data": sanitize_audit_data(after),
                "changed_fields": sanitize_audit_data(changed_fields or cls._changed_fields(before, after)),
                "metadata": sanitize_audit_data(metadata),
                "changes": sanitize_audit_data(changes),
                "ip": truncate_text(cls._ip(request, ctx), 45),
                "user_agent": truncate_text(cls._ua(request, ctx), 512),
                "http_method": truncate_text(getattr(request, "method", None) or getattr(ctx, "http_method", None), 12),
                "endpoint": truncate_text(getattr(request, "path", None) or getattr(ctx, "endpoint", None), 255),
                "status_code": status_code,
                "error_code": truncate_text(error_code, 80),
                "error_message": truncate_text(error_message, 512),
            }
            return AuditLog.objects.internal_create(**payload)
        except Exception as exc:
            cls.failure_count += 1
            logger.exception("Falha ao registrar auditoria: %s", exc)
            if isinstance(exc, ValidationError) and "Ação de auditoria inválida" in str(exc):
                raise
            if audit_required:
                raise ValidationError("Falha segura ao registrar auditoria obrigatória.") from exc
            return None

    @staticmethod
    def _normalize_action(action):
        action = str(action or AuditAction.AUDIT_INTERNAL_FAILURE).strip()
        action = AuditAction.LEGACY_MAP.get(action, action.upper())
        if action not in AuditAction.VALID:
            raise ValidationError(f"Ação de auditoria inválida: {action[:64]}")
        return action

    @staticmethod
    def _choice(value, enum):
        value = getattr(value, "value", value)
        if value in enum.values:
            return value
        raise ValidationError(f"Classificação de auditoria inválida: {value}")

    @staticmethod
    def _resolve_user(user, request, ctx):
        user = user or getattr(request, "user", None) or getattr(ctx, "user", None)
        return user if getattr(user, "is_authenticated", False) else None

    @staticmethod
    def _object_context(instance, app_label, model, object_id, object_repr):
        if instance is not None:
            meta = instance._meta
            app_label = app_label or meta.app_label
            model = model or meta.model_name
            object_id = object_id if object_id is not None else getattr(instance, "pk", None)
            object_repr = object_repr or str(instance)
        return app_label, model, str(object_id or "") or None, object_repr

    @staticmethod
    def _extract_empresa(instance):
        if instance is None:
            return None
        if instance._meta.label_lower == "cadastros.empresa":
            return instance
        return getattr(instance, "empresa", None) or getattr(instance, "Idempresa", None)

    @staticmethod
    def _extract_loja(instance):
        if instance is None:
            return None
        if instance._meta.label_lower == "cadastros.loja":
            return instance
        for attr in ("loja", "idloja", "Idloja"):
            loja = getattr(instance, attr, None)
            if loja is not None:
                return loja
        return None

    @staticmethod
    def _changed_fields(before, after):
        if isinstance(before, dict) and isinstance(after, dict):
            return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return []

    @staticmethod
    def _ip(request, ctx):
        if request is not None:
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return request.META.get("REMOTE_ADDR")
        return getattr(ctx, "ip", None)

    @staticmethod
    def _ua(request, ctx):
        if request is not None:
            return request.META.get("HTTP_USER_AGENT")
        return getattr(ctx, "user_agent", None)


def instance_snapshot(instance, exclude=None):
    if instance is None:
        return None
    exclude = set(exclude or [])
    data = model_to_dict(instance)
    for field in exclude | {"password", "token_key_hash", "key_hash"}:
        data.pop(field, None)
    return data

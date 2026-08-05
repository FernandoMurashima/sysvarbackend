import uuid

from .context import AuditContext, get_audit_context, parse_uuid, reset_audit_context, set_audit_context


def build_audit_context(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    sessao = getattr(request, "access_session", None)
    user = getattr(request, "user", None)
    correlation_id = parse_uuid(request.headers.get("X-Correlation-ID") or request.META.get("HTTP_X_CORRELATION_ID"))
    context = AuditContext(
        request_id=uuid.uuid4(),
        correlation_id=correlation_id,
        request=request,
        user=user if getattr(user, "is_authenticated", False) else None,
        empresa=getattr(user, "empresa", None) if getattr(user, "is_authenticated", False) else None,
        loja=getattr(user, "loja", None) if getattr(user, "is_authenticated", False) else None,
        session=sessao,
        device_id=getattr(sessao, "dispositivo_id", None) or request.headers.get("X-Device-ID"),
        ip=ip,
        user_agent=request.META.get("HTTP_USER_AGENT"),
        http_method=request.method,
        endpoint=request.path,
    )
    request.audit_request_id = context.request_id
    request.audit_correlation_id = context.correlation_id
    return context


def set_current_request(request):
    if request is None:
        return set_audit_context(None)
    return set_audit_context(build_audit_context(request))


def clear_current_request(token):
    reset_audit_context(token)

def get_current_request():
    ctx = get_audit_context()
    return getattr(ctx, "request", None)

def get_current_user():
    ctx = get_audit_context()
    if ctx and getattr(ctx.user, "is_authenticated", False):
        return ctx.user
    req = get_current_request()
    if req and hasattr(req, "user") and getattr(req.user, "is_authenticated", False):
        return req.user
    return None

def get_current_ip():
    ctx = get_audit_context()
    if ctx and ctx.ip:
        return ctx.ip
    req = get_current_request()
    if not req:
        return None
    ip = req.META.get("HTTP_X_FORWARDED_FOR")
    if ip:
        ip = ip.split(",")[0].strip()  # primeiro IP
    else:
        ip = req.META.get("REMOTE_ADDR")
    return ip

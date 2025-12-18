# auditoria/signals.py
from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed

from .models import AuditLog
from .middleware import get_current_request, get_current_user, get_current_ip

# Só auditamos esses apps de domínio (evita ruído/loops)
ALLOWED_APPS = {"cadastros", "produto"}  # adicione outros apps quando quiser

# ----------------- helpers -----------------
def _model_ident(instance):
    meta = instance._meta
    return meta.app_label, meta.model_name, str(getattr(instance, "pk", "") or "")

def _dict_clean(instance):
    data = model_to_dict(instance)
    for k in ("data_cadastro",):
        data.pop(k, None)
    return data

def _diff(old: dict, new: dict):
    changed = {}
    keys = set(old.keys()) | set(new.keys())
    for k in keys:
        if old.get(k) != new.get(k):
            changed[k] = [old.get(k), new.get(k)]
    return changed

def _ctx_from_request(req):
    ip = None
    ua = None
    if req:
        # Primeiro tenta via middleware; se não, pega o META
        ip = req.META.get("REMOTE_ADDR")
        ua = (req.META.get("HTTP_USER_AGENT") or "")[:400]
    else:
        ip = get_current_ip()
        r2 = get_current_request()
        if r2:
            ua = (r2.META.get("HTTP_USER_AGENT") or "")[:400]
    return ip, ua

# ----------------- CRUD signals -----------------
@receiver(pre_save)
def audit_presave_snapshot(sender, instance, **kwargs):
    if sender._meta.app_label not in ALLOWED_APPS:
        return
    if getattr(instance, "pk", None):
        try:
            current = sender.objects.get(pk=instance.pk)
            instance.__audit_old__ = _dict_clean(current)
        except sender.DoesNotExist:
            instance.__audit_old__ = {}
    else:
        instance.__audit_old__ = {}

@receiver(post_save)
def audit_postsave(sender, instance, created, **kwargs):
    if sender._meta.app_label not in ALLOWED_APPS:
        return

    app_label, model, obj_id = _model_ident(instance)
    req = get_current_request()
    user = get_current_user()
    ip, ua = _ctx_from_request(req)

    new_data = _dict_clean(instance)
    old_data = getattr(instance, "__audit_old__", {})

    if created:
        AuditLog.objects.create(
            action="create",
            app_label=app_label,
            model=model,
            object_id=obj_id,
            changes=new_data,
            user=user,
            ip=ip,
            user_agent=ua,
        )
    else:
        changes = _diff(old_data, new_data)
        if changes:
            AuditLog.objects.create(
                action="update",
                app_label=app_label,
                model=model,
                object_id=obj_id,
                changes=changes,
                user=user,
                ip=ip,
                user_agent=ua,
            )
    if hasattr(instance, "__audit_old__"):
        delattr(instance, "__audit_old__")

@receiver(pre_delete)
def audit_predelete(sender, instance, **kwargs):
    if sender._meta.app_label not in ALLOWED_APPS:
        return

    app_label, model, obj_id = _model_ident(instance)
    req = get_current_request()
    user = get_current_user()
    ip, ua = _ctx_from_request(req)
    snapshot = _dict_clean(instance)

    AuditLog.objects.create(
        action="delete",
        app_label=app_label,
        model=model,
        object_id=obj_id,
        changes=snapshot,
        user=user,
        ip=ip,
        user_agent=ua,
    )

# ----------------- auth (sessão) -----------------
@receiver(user_logged_in)
def audit_user_logged_in(sender, request, user, **kwargs):
    ip, ua = _ctx_from_request(request)
    AuditLog.objects.create(
        action="login",
        app_label="accounts",
        model="session",
        object_id=str(user.pk),
        changes={"auth": "session", "path": getattr(request, "path", None)},
        user=user,
        ip=ip,
        user_agent=ua,
    )

@receiver(user_logged_out)
def audit_user_logged_out(sender, request, user, **kwargs):
    ip, ua = _ctx_from_request(request)
    AuditLog.objects.create(
        action="logout",
        app_label="accounts",
        model="session",
        object_id=str(getattr(user, "pk", "") or ""),
        changes={"auth": "session", "path": getattr(request, "path", None)},
        user=user if user and getattr(user, "is_authenticated", False) else None,
        ip=ip,
        user_agent=ua,
    )

@receiver(user_login_failed)
def audit_user_login_failed(sender, credentials, request, **kwargs):
    ip, ua = _ctx_from_request(request)
    AuditLog.objects.create(
        action="custom",
        app_label="accounts",
        model="session",
        object_id=str(credentials.get("username") or ""),
        changes={"event": "login_failed", "auth": "session"},
        user=None,
        ip=ip,
        user_agent=ua,
    )

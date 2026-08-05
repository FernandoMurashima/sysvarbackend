import logging

from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed

from .models import AuditAction, AuditCategory, AuditResult, AuditSeverity
from .middleware import get_current_request, get_current_user, get_current_ip
from .services import AuditService

logger = logging.getLogger(__name__)

class AuditRegistry:
    _registry = {}

    @classmethod
    def register(cls, model, *, category=AuditCategory.SYSTEM, ignored_fields=None, actions=("create", "update", "delete"), empresa_getter=None, loja_getter=None, repr_getter=None):
        cls._registry[model] = {
            "category": category,
            "ignored_fields": set(ignored_fields or []),
            "actions": set(actions),
            "empresa_getter": empresa_getter,
            "loja_getter": loja_getter,
            "repr_getter": repr_getter,
        }

    @classmethod
    def config_for(cls, model):
        return cls._registry.get(model)

# ----------------- helpers -----------------
def _model_ident(instance):
    meta = instance._meta
    return meta.app_label, meta.model_name, str(getattr(instance, "pk", "") or "")

def _dict_clean(instance):
    cfg = AuditRegistry.config_for(instance.__class__) or {}
    data = model_to_dict(instance)
    for k in set(cfg.get("ignored_fields") or []) | {"data_cadastro", "created_at", "updated_at"}:
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
    if not AuditRegistry.config_for(sender):
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
    cfg = AuditRegistry.config_for(sender)
    if not cfg:
        return
    verb = "create" if created else "update"
    if verb not in cfg["actions"]:
        return

    app_label, model, obj_id = _model_ident(instance)
    req = get_current_request()
    user = get_current_user()
    new_data = _dict_clean(instance)
    old_data = getattr(instance, "__audit_old__", {})

    if created:
        AuditService.on_commit(
            action=AuditAction.OBJECT_CREATED,
            category=cfg["category"],
            result=AuditResult.SUCCESS,
            severity=AuditSeverity.INFO,
            instance=instance,
            empresa=cfg["empresa_getter"](instance) if cfg.get("empresa_getter") else None,
            loja=cfg["loja_getter"](instance) if cfg.get("loja_getter") else None,
            app_label=app_label,
            model=model,
            object_id=obj_id,
            after=new_data,
            changed_fields=list(new_data.keys()),
            user=user,
            request=req,
        )
    else:
        changes = _diff(old_data, new_data)
        if changes:
            AuditService.on_commit(
                action=AuditAction.OBJECT_UPDATED,
                category=cfg["category"],
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                instance=instance,
                empresa=cfg["empresa_getter"](instance) if cfg.get("empresa_getter") else None,
                loja=cfg["loja_getter"](instance) if cfg.get("loja_getter") else None,
                app_label=app_label,
                model=model,
                object_id=obj_id,
                before={k: v[0] for k, v in changes.items()},
                after={k: v[1] for k, v in changes.items()},
                changed_fields=list(changes.keys()),
                user=user,
                request=req,
            )
    if hasattr(instance, "__audit_old__"):
        delattr(instance, "__audit_old__")

@receiver(pre_delete)
def audit_predelete(sender, instance, **kwargs):
    cfg = AuditRegistry.config_for(sender)
    if not cfg or "delete" not in cfg["actions"]:
        return

    app_label, model, obj_id = _model_ident(instance)
    req = get_current_request()
    user = get_current_user()
    snapshot = _dict_clean(instance)

    AuditService.record(
        action=AuditAction.OBJECT_DELETED,
        category=cfg["category"],
        result=AuditResult.SUCCESS,
        severity=AuditSeverity.WARNING,
        instance=instance,
        empresa=cfg["empresa_getter"](instance) if cfg.get("empresa_getter") else None,
        loja=cfg["loja_getter"](instance) if cfg.get("loja_getter") else None,
        app_label=app_label,
        model=model,
        object_id=obj_id,
        before=snapshot,
        changed_fields=list(snapshot.keys()),
        user=user,
        request=req,
    )

# ----------------- auth (sessão) -----------------
@receiver(user_logged_in)
def audit_user_logged_in(sender, request, user, **kwargs):
    AuditService.security(
        action=AuditAction.USER_LOGIN,
        result=AuditResult.SUCCESS,
        severity=AuditSeverity.INFO,
        app_label="accounts",
        model="session",
        object_id=str(user.pk),
        metadata={"auth": "django_signal"},
        request=request,
        user=user,
        status_code=200,
    )

@receiver(user_logged_out)
def audit_user_logged_out(sender, request, user, **kwargs):
    AuditService.security(
        action=AuditAction.USER_LOGOUT,
        result=AuditResult.SUCCESS,
        app_label="accounts",
        model="session",
        object_id=str(getattr(user, "pk", "") or ""),
        metadata={"auth": "django_signal"},
        user=user if user and getattr(user, "is_authenticated", False) else None,
        request=request,
        status_code=200,
    )

@receiver(user_login_failed)
def audit_user_login_failed(sender, credentials, request, **kwargs):
    AuditService.security(
        action=AuditAction.USER_LOGIN_DENIED,
        result=AuditResult.DENIED,
        severity=AuditSeverity.WARNING,
        app_label="accounts",
        model="session",
        object_id=str(credentials.get("username") or ""),
        metadata={"username": credentials.get("username"), "auth": "django_signal"},
        request=request,
        status_code=401,
    )


def _empresa(instance):
    return getattr(instance, "empresa", None) or (instance if instance._meta.label_lower == "cadastros.empresa" else None)


def _loja(instance):
    return instance if instance._meta.label_lower == "cadastros.loja" else getattr(instance, "loja", None)


def register_default_audit_models():
    try:
        from accounts.models import PerfilAcesso, PerfilModuloPermissao
        from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Loja
    except Exception:
        logger.exception("Não foi possível registrar models auditados.")
        return
    AuditRegistry.register(Empresa, category=AuditCategory.CADASTRO, ignored_fields={"documento"})
    AuditRegistry.register(Loja, category=AuditCategory.CADASTRO, empresa_getter=_empresa, ignored_fields={"cnpj"})
    AuditRegistry.register(PerfilAcesso, category=AuditCategory.ACCESS, empresa_getter=_empresa)
    AuditRegistry.register(PerfilModuloPermissao, category=AuditCategory.ACCESS, empresa_getter=lambda o: o.perfil.empresa)
    AuditRegistry.register(EmpresaContrato, category=AuditCategory.CONTRACT, empresa_getter=_empresa)
    AuditRegistry.register(EmpresaModulo, category=AuditCategory.CONTRACT, empresa_getter=_empresa)


register_default_audit_models()

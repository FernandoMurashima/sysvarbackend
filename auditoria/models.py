import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone


class AuditCategory(models.TextChoices):
    SECURITY = "SECURITY", "Segurança"
    ACCESS = "ACCESS", "Acesso"
    CONTRACT = "CONTRACT", "Contrato"
    USER_MANAGEMENT = "USER_MANAGEMENT", "Usuários"
    CADASTRO = "CADASTRO", "Cadastros"
    PRODUCT = "PRODUCT", "Produtos"
    PURCHASE = "PURCHASE", "Compras"
    STOCK = "STOCK", "Estoque"
    SALE = "SALE", "Vendas"
    FISCAL = "FISCAL", "Fiscal"
    FINANCIAL = "FINANCIAL", "Financeiro"
    ACCOUNTING = "ACCOUNTING", "Contabilidade"
    PRODUCTION = "PRODUCTION", "Produção"
    DISTRIBUTION = "DISTRIBUTION", "Distribuição"
    REPORT = "REPORT", "Relatórios"
    SYSTEM = "SYSTEM", "Sistema"
    INTEGRATION = "INTEGRATION", "Integração"


class AuditResult(models.TextChoices):
    SUCCESS = "SUCCESS", "Sucesso"
    FAILURE = "FAILURE", "Falha"
    DENIED = "DENIED", "Negado"
    PENDING = "PENDING", "Pendente"
    ROLLED_BACK = "ROLLED_BACK", "Rollback"


class AuditSeverity(models.TextChoices):
    INFO = "INFO", "Informação"
    WARNING = "WARNING", "Alerta"
    ERROR = "ERROR", "Erro"
    CRITICAL = "CRITICAL", "Crítico"


class AuditOrigin(models.TextChoices):
    API = "API", "API"
    WEB = "WEB", "Web"
    PDV = "PDV", "PDV"
    OFFLINE_SYNC = "OFFLINE_SYNC", "Sincronização offline"
    COMMAND = "COMMAND", "Command"
    IMPORT = "IMPORT", "Importação"
    INTEGRATION = "INTEGRATION", "Integração"
    SYSTEM = "SYSTEM", "Sistema"


class AuditAction:
    USER_LOGIN = "USER_LOGIN"
    USER_LOGIN_DENIED = "USER_LOGIN_DENIED"
    USER_LOGOUT = "USER_LOGOUT"
    USER_ACTIVATED = "USER_ACTIVATED"
    USER_INACTIVATED = "USER_INACTIVATED"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_DELETED = "USER_DELETED"
    USER_PASSWORD_RESET = "USER_PASSWORD_RESET"
    USER_PASSWORD_CHANGED = "USER_PASSWORD_CHANGED"
    USER_PROFILE_CHANGED = "USER_PROFILE_CHANGED"
    USER_STORE_ACCESS_CHANGED = "USER_STORE_ACCESS_CHANGED"
    USER_OVERRIDE_CHANGED = "USER_OVERRIDE_CHANGED"
    USER_SESSIONS_CLOSED = "USER_SESSIONS_CLOSED"
    USER_OPERATION_DENIED = "USER_OPERATION_DENIED"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_REPLACED = "SESSION_REPLACED"
    SESSION_CLOSED = "SESSION_CLOSED"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"
    SESSION_LIMIT_REACHED = "SESSION_LIMIT_REACHED"
    SESSION_CLOSE_DENIED = "SESSION_CLOSE_DENIED"
    CONTRACT_CREATED = "CONTRACT_CREATED"
    CONTRACT_UPDATED = "CONTRACT_UPDATED"
    CONTRACT_STATUS_CHANGED = "CONTRACT_STATUS_CHANGED"
    CONTRACT_LIMIT_CHANGED = "CONTRACT_LIMIT_CHANGED"
    CONTRACT_SUSPENDED = "CONTRACT_SUSPENDED"
    CONTRACT_REACTIVATED = "CONTRACT_REACTIVATED"
    CONTRACT_SUSPENSION_DENIED = "CONTRACT_SUSPENSION_DENIED"
    CONTRACT_REACTIVATION_DENIED = "CONTRACT_REACTIVATION_DENIED"
    MASTER_TRANSFERRED = "MASTER_TRANSFERRED"
    MASTER_TRANSFER_DENIED = "MASTER_TRANSFER_DENIED"
    PROFILE_CREATED = "PROFILE_CREATED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    PROFILE_INACTIVATED = "PROFILE_INACTIVATED"
    PROFILE_DEFAULT_CHANGED = "PROFILE_DEFAULT_CHANGED"
    PERMISSION_UPDATED = "PERMISSION_UPDATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    STORE_CREATED = "STORE_CREATED"
    STORE_UPDATED = "STORE_UPDATED"
    STORE_ACTIVATED = "STORE_ACTIVATED"
    STORE_DEACTIVATED = "STORE_DEACTIVATED"
    STORE_CLOSED = "STORE_CLOSED"
    STORE_REOPENED = "STORE_REOPENED"
    STORE_FISCAL_CONFIG_UPDATED = "STORE_FISCAL_CONFIG_UPDATED"
    STORE_NUMBERING_UPDATED = "STORE_NUMBERING_UPDATED"
    STORE_NEGATIVE_STOCK_POLICY_UPDATED = "STORE_NEGATIVE_STOCK_POLICY_UPDATED"
    STORE_OPERATION_DENIED = "STORE_OPERATION_DENIED"
    AUDIT_EXPORT = "AUDIT_EXPORT"
    AUDIT_ACCESS_DENIED = "AUDIT_ACCESS_DENIED"
    LEGACY_EVENT = "LEGACY_EVENT"
    OBJECT_CREATED = "OBJECT_CREATED"
    OBJECT_UPDATED = "OBJECT_UPDATED"
    OBJECT_DELETED = "OBJECT_DELETED"
    AUDIT_INTERNAL_FAILURE = "AUDIT_INTERNAL_FAILURE"

    LEGACY_MAP = {
        "create": OBJECT_CREATED,
        "update": OBJECT_UPDATED,
        "delete": OBJECT_DELETED,
        "login": USER_LOGIN,
        "logout": USER_LOGOUT,
        "custom": AUDIT_INTERNAL_FAILURE,
        "legacy_register_user": USER_CREATED,
        "user_activate": USER_ACTIVATED,
        "user_deactivate": USER_INACTIVATED,
        "session_closed": SESSION_CLOSED,
        "session_close_denied": SESSION_CLOSE_DENIED,
        "session_limit_block": SESSION_LIMIT_REACHED,
        "session_login": USER_LOGIN,
        "contract_create": CONTRACT_CREATED,
        "contract_update": CONTRACT_UPDATED,
        "company_module_create": CONTRACT_UPDATED,
        "company_module_update": CONTRACT_UPDATED,
        "master_transfer_denied": MASTER_TRANSFER_DENIED,
        "master_transfer": MASTER_TRANSFERRED,
        "profile_set_default": PROFILE_DEFAULT_CHANGED,
        "profile_activate": PROFILE_UPDATED,
        "profile_deactivate": PROFILE_INACTIVATED,
        "profile_delete": PROFILE_INACTIVATED,
    }

    VALID = {
        USER_LOGIN, USER_LOGIN_DENIED, USER_LOGOUT, USER_ACTIVATED, USER_INACTIVATED,
        USER_CREATED, USER_UPDATED, USER_DELETED, USER_PASSWORD_RESET, USER_PASSWORD_CHANGED,
        USER_PROFILE_CHANGED, USER_STORE_ACCESS_CHANGED, USER_OVERRIDE_CHANGED, USER_SESSIONS_CLOSED,
        USER_OPERATION_DENIED, SESSION_CREATED, SESSION_REPLACED,
        SESSION_CLOSED, SESSION_TIMEOUT, SESSION_LIMIT_REACHED, SESSION_CLOSE_DENIED,
        CONTRACT_CREATED, CONTRACT_UPDATED, CONTRACT_STATUS_CHANGED, CONTRACT_LIMIT_CHANGED,
        CONTRACT_SUSPENDED, CONTRACT_REACTIVATED, CONTRACT_SUSPENSION_DENIED, CONTRACT_REACTIVATION_DENIED,
        MASTER_TRANSFERRED, MASTER_TRANSFER_DENIED, PROFILE_CREATED, PROFILE_UPDATED,
        PROFILE_INACTIVATED, PROFILE_DEFAULT_CHANGED, PERMISSION_UPDATED, PERMISSION_DENIED,
        STORE_CREATED, STORE_UPDATED, STORE_ACTIVATED, STORE_DEACTIVATED, STORE_CLOSED,
        STORE_REOPENED, STORE_FISCAL_CONFIG_UPDATED, STORE_NUMBERING_UPDATED,
        STORE_NEGATIVE_STOCK_POLICY_UPDATED, STORE_OPERATION_DENIED,
        AUDIT_EXPORT, AUDIT_ACCESS_DENIED, LEGACY_EVENT, OBJECT_CREATED, OBJECT_UPDATED, OBJECT_DELETED,
        AUDIT_INTERNAL_FAILURE,
    }


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Logs de auditoria são imutáveis.")

    def delete(self):
        raise ValidationError("Logs de auditoria são imutáveis.")

    def hard_delete_for_retention(self):
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Logs de auditoria são imutáveis.")


class AuditLogManager(models.Manager.from_queryset(AuditLogQuerySet)):
    def create(self, **kwargs):
        if not kwargs.pop("_audit_internal", False):
            raise ValidationError("Use AuditService para criar logs de auditoria.")
        return super().create(**kwargs)

    def internal_create(self, **kwargs):
        kwargs["_audit_internal"] = True
        return self.create(**kwargs)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False, update_fields=None, unique_fields=None):
        raise ValidationError("Use AuditService para criar logs de auditoria.")

    def internal_bulk_create(self, objs, **kwargs):
        for obj in objs:
            obj._audit_internal_bulk = True
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Logs de auditoria são imutáveis.")

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        raise ValidationError("Use AuditService para criar logs de auditoria.")

    def get_or_create(self, defaults=None, **kwargs):
        raise ValidationError("Use AuditService para criar logs de auditoria.")


class AuditLog(models.Model):
    """
    Log genérico de auditoria.
    Mantém campos dimensionados para não estourar no MySQL (inclusive IPv6/UA longos).
    """
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    request_id = models.UUIDField(null=True, blank=True, db_index=True)
    correlation_id = models.UUIDField(null=True, blank=True, db_index=True)

    empresa = models.ForeignKey(
        "cadastros.Empresa",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    empresa_id_snapshot = models.CharField(max_length=64, null=True, blank=True)
    empresa_nome_snapshot = models.CharField(max_length=160, null=True, blank=True)

    loja = models.ForeignKey(
        "cadastros.Loja",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    loja_id_snapshot = models.CharField(max_length=64, null=True, blank=True)
    loja_nome_snapshot = models.CharField(max_length=120, null=True, blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_logs",
    )
    user_id_snapshot = models.CharField(max_length=64, null=True, blank=True)
    username_snapshot = models.CharField(max_length=150, null=True, blank=True)
    user_nome_snapshot = models.CharField(max_length=180, null=True, blank=True)

    session_id = models.UUIDField(null=True, blank=True, db_index=True)
    device_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)

    action = models.CharField(max_length=64, db_index=True)
    category = models.CharField(max_length=32, choices=AuditCategory.choices, default=AuditCategory.SYSTEM, db_index=True)
    result = models.CharField(max_length=16, choices=AuditResult.choices, default=AuditResult.SUCCESS, db_index=True)
    severity = models.CharField(max_length=16, choices=AuditSeverity.choices, default=AuditSeverity.INFO, db_index=True)
    origin = models.CharField(max_length=20, choices=AuditOrigin.choices, default=AuditOrigin.API, db_index=True)

    app_label = models.CharField(max_length=50, db_index=True)
    model = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    object_repr = models.CharField(max_length=255, null=True, blank=True)

    before_data = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    after_data = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    changed_fields = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    metadata = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    changes = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)

    ip = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.CharField(max_length=512, null=True, blank=True)
    http_method = models.CharField(max_length=12, null=True, blank=True, db_index=True)
    endpoint = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    error_code = models.CharField(max_length=80, null=True, blank=True)
    error_message = models.CharField(max_length=512, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    objects = AuditLogManager()

    class Meta:
        db_table = "auditoria_auditlog"
        indexes = [
            models.Index(fields=["app_label", "model"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["empresa", "created_at"], name="aud_emp_created_idx"),
            models.Index(fields=["empresa", "category", "created_at"], name="aud_emp_cat_created_idx"),
            models.Index(fields=["empresa", "user", "created_at"], name="aud_emp_user_created_idx"),
            models.Index(fields=["empresa", "app_label", "model", "object_id"], name="aud_emp_obj_idx"),
            models.Index(fields=["loja", "created_at"], name="aud_loja_created_idx"),
            models.Index(fields=["result", "severity", "created_at"], name="aud_res_sev_created_idx"),
        ]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("_audit_internal", False):
            raise ValidationError("Logs de auditoria são imutáveis.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Logs de auditoria são imutáveis.")

    def __str__(self):
        base = f"[{self.action}] {self.app_label}.{self.model}"
        if self.object_id:
            base += f" #{self.object_id}"
        return base

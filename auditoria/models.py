from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder


class AuditLog(models.Model):
    """
    Log genérico de auditoria.
    Mantém campos dimensionados para não estourar no MySQL (inclusive IPv6/UA longos).
    """
    # Comprimentos dimensionados (e index úteis)
    action = models.CharField(max_length=32, db_index=True)            # ex.: set_forma_pagamento, aprovar, custom...
    app_label = models.CharField(max_length=50, db_index=True)         # "compras", "financeiro", etc.
    model = models.CharField(max_length=100)                            # nome do modelo
    object_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    # JSON com {campo: [antes, depois]} ou snapshot livre
    changes = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)

    # Quem fez
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_logs",
    )
    # Char para suportar vazio + IPv6 (até 45 chars)
    ip = models.CharField(max_length=45, null=True, blank=True)
    # Alguns user-agents estouram fácil; 512 é seguro
    user_agent = models.CharField(max_length=512, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "auditoria_auditlog"
        indexes = [
            models.Index(fields=["app_label", "model"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        base = f"[{self.action}] {self.app_label}.{self.model}"
        if self.object_id:
            base += f" #{self.object_id}"
        return base

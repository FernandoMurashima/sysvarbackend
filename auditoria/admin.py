from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import json

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "empresa_nome_snapshot", "loja_nome_snapshot", "username_snapshot", "category", "action", "result", "severity", "app_label", "model")
    list_filter = ("category", "result", "severity", "origin", "app_label", "model", "created_at")
    search_fields = ("event_id", "request_id", "object_id", "username_snapshot", "ip", "endpoint")
    readonly_fields = [field.name for field in AuditLog._meta.fields] + ["json_pretty"]
    ordering = ("-created_at",)

    fieldsets = (
        (None, {
            "fields": (
                ("event_id", "created_at"),
                ("category", "action", "result", "severity", "origin"),
                ("empresa_nome_snapshot", "loja_nome_snapshot", "username_snapshot"),
                ("app_label", "model", "object_id"),
                ("request_id", "correlation_id", "session_id", "device_id"),
                ("ip", "http_method", "status_code"),
                "endpoint", "user_agent", "json_pretty",
            )
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def json_pretty(self, obj):
        try:
            text = json.dumps({
                "before_data": obj.before_data,
                "after_data": obj.after_data,
                "changed_fields": obj.changed_fields,
                "metadata": obj.metadata,
                "changes_legacy": obj.changes,
            }, ensure_ascii=False, indent=2)
        except Exception:
            text = ""
        return format_html("<pre style='max-height:420px;overflow:auto;margin:0'>{}</pre>", mark_safe(text))

    json_pretty.short_description = "Dados"

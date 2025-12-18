from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import json

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "app_label", "model", "object_id", "user", "ip")
    list_filter = ("action", "app_label", "model", "user", "created_at")
    search_fields = ("object_id", "user__username", "ip", "user_agent")
    readonly_fields = (
        "created_at", "action", "app_label", "model", "object_id", "user",
        "ip", "user_agent", "changes_pretty"
    )
    ordering = ("-created_at",)

    fieldsets = (
        (None, {
            "fields": (
                ("action", "created_at"),
                ("app_label", "model", "object_id"),
                ("user", "ip"),
                "user_agent",
                "changes_pretty",
            )
        }),
    )

    def changes_pretty(self, obj):
        try:
            text = json.dumps(obj.changes, ensure_ascii=False, indent=2)
        except Exception:
            text = str(obj.changes)
        return format_html("<pre style='max-height:420px;overflow:auto;margin:0'>{}</pre>", mark_safe(text))

    changes_pretty.short_description = "Changes (JSON)"

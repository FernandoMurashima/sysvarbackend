from rest_framework import serializers
from .models import AuditLog

class AuditLogSerializer(serializers.ModelSerializer):
    user_username = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "app_label",
            "model",
            "object_id",
            "changes",
            "user",
            "user_username",
            "ip",
            "user_agent",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_username(self, obj):
        return getattr(obj.user, "username", None)

from rest_framework import serializers
from .models import AuditLog

class AuditLogSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="username_snapshot", read_only=True)
    empresa_nome = serializers.CharField(source="empresa_nome_snapshot", read_only=True)
    loja_nome = serializers.CharField(source="loja_nome_snapshot", read_only=True)
    entidade = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "event_id", "created_at", "user_username", "empresa_nome",
            "loja_nome", "category", "action", "result", "severity", "entidade",
            "object_id", "object_repr", "ip", "request_id",
        ]
        read_only_fields = fields

    def get_entidade(self, obj):
        return ".".join(part for part in [obj.app_label, obj.model] if part)


class AuditLogDetailSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="username_snapshot", read_only=True)
    empresa_nome = serializers.CharField(source="empresa_nome_snapshot", read_only=True)
    loja_nome = serializers.CharField(source="loja_nome_snapshot", read_only=True)
    entidade = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "event_id", "request_id", "correlation_id", "created_at",
            "empresa", "empresa_id_snapshot", "empresa_nome_snapshot", "empresa_nome",
            "loja", "loja_id_snapshot", "loja_nome_snapshot", "loja_nome",
            "user", "user_id_snapshot", "username_snapshot", "user_username", "user_nome_snapshot",
            "session_id", "device_id", "action", "category", "result", "severity", "origin",
            "app_label", "model", "entidade", "object_id", "object_repr",
            "before_data", "after_data", "changed_fields", "metadata", "ip", "user_agent",
            "http_method", "endpoint", "status_code", "error_code", "error_message",
        ]
        read_only_fields = fields

    def get_entidade(self, obj):
        return ".".join(part for part in [obj.app_label, obj.model] if part)

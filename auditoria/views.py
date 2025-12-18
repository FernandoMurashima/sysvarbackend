from rest_framework import viewsets, permissions, filters
from django_filters import rest_framework as df

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogFilter(df.FilterSet):
    # filtros de data
    created_at_after = df.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at_before = df.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = AuditLog
        fields = {
            "action": ["exact"],
            "app_label": ["exact", "icontains"],
            "model": ["exact", "icontains"],
            "object_id": ["exact", "icontains"],
            "user": ["exact"],
            "ip": ["exact", "icontains"],
        }


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Somente leitura. Protegido para staff/admin.
    Filtros:
      - action=create|update|delete|login|logout|custom
      - app_label, model, object_id, user, ip
      - created_at_after, created_at_before (ISO 8601)
    Busca:
      - object_id, user.username, ip, user_agent
    """
    queryset = AuditLog.objects.all().order_by("-created_at")
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [df.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = AuditLogFilter
    search_fields = ["object_id", "user__username", "ip", "user_agent"]
    ordering_fields = ["created_at", "action", "app_label", "model"]

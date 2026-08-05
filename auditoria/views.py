import csv

from django.db.models import Count, Q
from django.http import HttpResponse
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters import rest_framework as df

from accounts.services.effective_access import EDIT, EffectiveAccessService
from .models import AuditAction, AuditCategory, AuditLog, AuditResult, AuditSeverity
from .serializers import AuditLogDetailSerializer, AuditLogSerializer
from .services import AuditService


class AuditLogFilter(df.FilterSet):
    created_at_after = df.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at_before = df.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    empresa = df.NumberFilter(method="filter_empresa")
    loja = df.NumberFilter(method="filter_loja")

    def filter_empresa(self, queryset, name, value):
        user = self.request.user
        if user.is_superuser:
            return queryset.filter(empresa_id=value)
        return queryset

    def filter_loja(self, queryset, name, value):
        user = self.request.user
        if user.is_superuser:
            return queryset.filter(loja_id=value)
        service = EffectiveAccessService(user)
        if service.is_company_master():
            return queryset.filter(loja_id=value)
        allowed = service.allowed_store_ids()
        if allowed is None or int(value) in allowed:
            return queryset.filter(loja_id=value)
        return queryset.none()

    class Meta:
        model = AuditLog
        fields = {
            "action": ["exact"],
            "category": ["exact"],
            "result": ["exact"],
            "severity": ["exact"],
            "origin": ["exact"],
            "app_label": ["exact"],
            "model": ["exact"],
            "object_id": ["exact", "icontains"],
            "empresa": ["exact"],
            "loja": ["exact"],
            "user": ["exact"],
            "ip": ["exact", "icontains"],
            "request_id": ["exact"],
            "correlation_id": ["exact"],
            "session_id": ["exact"],
            "device_id": ["exact", "icontains"],
            "http_method": ["exact"],
            "endpoint": ["icontains"],
            "status_code": ["exact"],
        }


class CanViewAuditLogs:
    message = "Usuário sem permissão para consultar auditoria."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        service = EffectiveAccessService(user)
        allowed = service.is_company_master() or service.has_module_access("auditoria")
        if not allowed:
            AuditService.denied(
                AuditAction.AUDIT_ACCESS_DENIED,
                category=AuditCategory.SECURITY,
                request=request,
                user=user,
                app_label="auditoria",
                model="auditlog",
                metadata={"endpoint": request.path},
            )
        return allowed


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("empresa", "loja", "user").all().order_by("-created_at")
    serializer_class = AuditLogSerializer
    permission_classes = [CanViewAuditLogs]
    filter_backends = [df.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = AuditLogFilter
    search_fields = [
        "event_id", "request_id", "correlation_id", "username_snapshot",
        "user_nome_snapshot", "empresa_nome_snapshot", "loja_nome_snapshot",
        "object_repr", "object_id", "ip", "endpoint", "error_message",
    ]
    ordering_fields = ["created_at", "category", "action", "result", "severity", "user", "empresa"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AuditLogDetailSerializer
        return AuditLogSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        service = EffectiveAccessService(user)
        requested_empresa = self.request.query_params.get("empresa")
        requested_loja = self.request.query_params.get("loja")
        if user.is_superuser:
            return qs
        empresa_id = getattr(user, "empresa_id", None)
        if not empresa_id:
            return qs.none()
        if requested_empresa and str(requested_empresa) != str(empresa_id):
            AuditService.denied(
                AuditAction.AUDIT_ACCESS_DENIED,
                category=AuditCategory.SECURITY,
                request=self.request,
                user=user,
                app_label="auditoria",
                model="auditlog",
                metadata={"requested_empresa": requested_empresa},
            )
        qs = qs.filter(empresa_id=empresa_id)
        if service.is_company_master():
            return qs
        allowed_store_ids = service.allowed_store_ids()
        if allowed_store_ids is not None:
            qs = qs.filter(Q(loja_id__isnull=True) | Q(loja_id__in=allowed_store_ids))
            if requested_loja and int(requested_loja) not in allowed_store_ids:
                AuditService.denied(
                    AuditAction.AUDIT_ACCESS_DENIED,
                    category=AuditCategory.SECURITY,
                    request=self.request,
                    user=user,
                    app_label="auditoria",
                    model="auditlog",
                    metadata={"requested_loja": requested_loja},
                )
        return qs

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        rows = self.filter_queryset(self.get_queryset()).aggregate(
            total=Count("id"),
            success=Count("id", filter=Q(result=AuditResult.SUCCESS)),
            failure=Count("id", filter=Q(result=AuditResult.FAILURE)),
            denied=Count("id", filter=Q(result=AuditResult.DENIED)),
            critical=Count("id", filter=Q(severity=AuditSeverity.CRITICAL)),
        )
        return Response(rows)

    @action(detail=False, methods=["get"], url_path="exportar")
    def exportar(self, request):
        user = request.user
        service = EffectiveAccessService(user)
        if not (user.is_superuser or service.is_company_master() or service.has_module_access("auditoria", EDIT)):
            AuditService.denied(AuditAction.AUDIT_ACCESS_DENIED, category=AuditCategory.SECURITY, request=request, user=user, app_label="auditoria", model="auditlog", metadata={"export": True})
            raise PermissionDenied("Sem permissão para exportar auditoria.")
        limit = 5000
        qs = self.filter_queryset(self.get_queryset())[:limit]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="auditoria.csv"'
        writer = csv.writer(response)
        writer.writerow(["data", "empresa", "loja", "usuario", "categoria", "acao", "resultado", "severidade", "entidade", "objeto", "ip", "request_id"])
        for item in qs:
            writer.writerow([
                item.created_at.isoformat(), item.empresa_nome_snapshot, item.loja_nome_snapshot,
                item.username_snapshot, item.category, item.action, item.result, item.severity,
                f"{item.app_label}.{item.model}", item.object_repr or item.object_id, item.ip, item.request_id,
            ])
        AuditService.success(AuditAction.AUDIT_EXPORT, category=AuditCategory.SECURITY, request=request, user=user, app_label="auditoria", model="auditlog", metadata={"limit": limit})
        return response

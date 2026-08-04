from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.db.models import Count
from rest_framework import viewsets, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.serializers import AuthTokenSerializer

from rest_framework.permissions import IsAuthenticated

from auditoria.models import AuditLog
from accounts.services.effective_access import EffectiveAccessService, MasterTransferService, ProfileDefaultService, audit_event, increment_permissions_version
from accounts.services.profiles import visible_profile_names_for_company
from .permissions import CanManageAccessProfiles, CanManageCompanyUsers
from .serializers import (
    EmpresaContratoSerializer,
    EmpresaModuloSerializer,
    ModuloSistemaSerializer,
    PerfilAcessoSerializer,
    UserSerializer,
)
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, ModuloSistema
from .models import PerfilAcesso

User = get_user_model()



# ---- Health (público) ----
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health(request):
    return JsonResponse({"status": "ok", "app": "sysvar2"})

# ---- Register legado (bloqueado para uso público) ----
class RegisterView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = UserSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        audit_event("legacy_register_user", request, request.user, "user", user.pk, {"username": user.username})
        return Response({"message": "Usuário criado com sucesso.", "user": UserSerializer(user, context={"request": request}).data}, status=201)

# ---- Login (público) → token ----
class TokenLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AuthTokenSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if not user.is_superuser:
            access = EffectiveAccessService(user)
            state = access.contract_state()
            if not state.active:
                return Response({"non_field_errors": [state.reason or "Acesso indisponível."]}, status=400)
            if not access.is_company_master() and not getattr(user, "perfil_principal_id", None):
                return Response({"non_field_errors": ["Usuário sem perfil de acesso."]}, status=400)
        token, created = Token.objects.get_or_create(user=user)

        # auditoria
        try:
            AuditLog.objects.create(
                action="login",
                app_label="accounts",
                model="token",
                object_id=str(user.pk),
                changes={"auth": "token", "token_created": bool(created)},
                user=user,
                ip=(request.META.get("REMOTE_ADDR") or "")[:45],
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
            )
        except Exception:
            pass

        return Response({"token": token.key, "user": UserSerializer(user).data | EffectiveAccessService(user).session_payload()})

# ---- Logout (autenticado) → revoga todos os tokens do usuário ----
class TokenLogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        Token.objects.filter(user=user).delete()
        try:
            AuditLog.objects.create(
                action="logout",
                app_label="accounts",
                model="token",
                object_id=str(user.pk),
                changes={"auth": "token"},
                user=user,
                ip=(request.META.get("REMOTE_ADDR") or "")[:45],
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
            )
        except Exception:
            pass
        return Response({"detail": "logged out"})

# ---- Users CRUD + /users/me ----
class UserViewSet(viewsets.ModelViewSet):
    """
    /api/accounts/users/ -> CRUD (somente staff)
    /api/accounts/users/me/ -> dados do usuário logado (qualquer autenticado)
    """
    queryset = (
        User.objects
        .select_related("empresa", "loja")
        .prefetch_related("lojas", "module_permissions", "field_permissions")
        .all()
        .order_by("id")
    )
    serializer_class = UserSerializer
    permission_classes = [CanManageCompanyUsers]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["id", "username", "date_joined"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            return qs.filter(empresa_id=empresa_id)
        return qs.none()

    def perform_destroy(self, instance):
        user = self.request.user
        if not user.is_superuser:
            raise PermissionDenied("Usuários de empresas devem ser inativados, não excluídos.")
        if not user.is_superuser and instance.empresa_id != getattr(user, "empresa_id", None):
            raise PermissionDenied("Você só pode excluir usuários da sua empresa.")
        if EffectiveAccessService(instance).is_company_master():
            raise PermissionDenied("Transfira o master antes de excluir este usuário.")
        instance.delete()

    @action(detail=True, methods=["post"], permission_classes=[CanManageCompanyUsers], url_path="ativar")
    def ativar(self, request, pk=None):
        user = self.get_object()
        serializer = self.get_serializer(user, data={"is_active": True}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        Token.objects.filter(user=user).delete()
        audit_event("user_activate", request, request.user, "user", user.pk)
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"], permission_classes=[CanManageCompanyUsers], url_path="inativar")
    def inativar(self, request, pk=None):
        user = self.get_object()
        if EffectiveAccessService(user).is_company_master():
            raise PermissionDenied("Transfira o master antes de inativar este usuário.")
        user.is_active = False
        user.save(update_fields=["is_active"])
        Token.objects.filter(user=user).delete()
        if user.empresa_id:
            increment_permissions_version(user.empresa)
        audit_event("user_deactivate", request, request.user, "user", user.pk)
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"], permission_classes=[CanManageCompanyUsers], url_path="desativar")
    def desativar(self, request, pk=None):
        return self.inativar(request, pk=pk)

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data | EffectiveAccessService(request.user).session_payload())

class UserMeView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response(UserSerializer(request.user).data | EffectiveAccessService(request.user).session_payload())


class ModuloSistemaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ModuloSistema.objects.all().order_by("ordem", "nome")
    serializer_class = ModuloSistemaSerializer
    permission_classes = [permissions.IsAuthenticated]


class EmpresaContratoViewSet(viewsets.ModelViewSet):
    queryset = EmpresaContrato.objects.select_related("empresa", "usuario_master").all()
    serializer_class = EmpresaContratoSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser:
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(self.request.user, "empresa_id", None)
        return qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

    def perform_update(self, serializer):
        obj = serializer.save()
        obj.incrementar_versao()
        audit_event("contract_update", self.request, self.request.user, "contrato", obj.pk)

    @action(detail=True, methods=["post"], url_path="transferir-master")
    def transferir_master(self, request, pk=None):
        contrato = self.get_object()
        user_id = request.data.get("usuario_master") or request.data.get("user_id")
        new_master = User.objects.get(pk=user_id)
        MasterTransferService(request.user, contrato.empresa, new_master, request).transfer()
        return Response(self.get_serializer(contrato).data)


class EmpresaModuloViewSet(viewsets.ModelViewSet):
    queryset = EmpresaModulo.objects.select_related("empresa", "modulo").all()
    serializer_class = EmpresaModuloSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser:
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(self.request.user, "empresa_id", None)
        return qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

    def perform_create(self, serializer):
        obj = serializer.save()
        from accounts.services.effective_access import increment_permissions_version
        increment_permissions_version(obj.empresa)

    def perform_update(self, serializer):
        obj = serializer.save()
        from accounts.services.effective_access import increment_permissions_version
        increment_permissions_version(obj.empresa)


class PerfilAcessoViewSet(viewsets.ModelViewSet):
    queryset = PerfilAcesso.objects.annotate(usuarios_count=Count("usuarios")).prefetch_related("permissoes_modulos__modulo").order_by("empresa_id", "nome")
    serializer_class = PerfilAcessoSerializer
    permission_classes = [CanManageAccessProfiles]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser:
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(self.request.user, "empresa_id", None)
        if not empresa_id:
            return qs.none()
        visible_names = visible_profile_names_for_company(self.request.user.empresa)
        return qs.filter(empresa_id=empresa_id, nome__in=visible_names)

    @action(detail=True, methods=["post"], url_path="duplicar")
    def duplicar(self, request, pk=None):
        perfil = self.get_object()
        novo = PerfilAcesso.objects.create(
            empresa=perfil.empresa,
            nome=request.data.get("nome") or f"{perfil.nome} (cópia)",
            descricao=perfil.descricao,
            ativo=True,
            padrao=False,
        )
        for perm in perfil.permissoes_modulos.all():
            novo.permissoes_modulos.create(modulo=perm.modulo, acesso=perm.acesso)
        return Response(self.get_serializer(novo).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="definir-padrao")
    def definir_padrao(self, request, pk=None):
        perfil = self.get_object()
        perfil = ProfileDefaultService(request.user, perfil, request).set_default()
        return Response(self.get_serializer(perfil).data)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        perfil = self.get_object()
        perfil.ativo = True
        perfil.save(update_fields=["ativo", "updated_at"])
        increment_permissions_version(perfil.empresa)
        audit_event("profile_activate", request, request.user, "perfil_acesso", perfil.pk)
        return Response(self.get_serializer(perfil).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        perfil = self.get_object()
        if perfil.padrao:
            raise PermissionDenied("Defina outro perfil padrão antes de inativar este perfil.")
        if perfil.usuarios.filter(is_active=True).exists():
            raise PermissionDenied("Perfil em uso por usuários ativos não pode ser inativado.")
        perfil.ativo = False
        perfil.save(update_fields=["ativo", "updated_at"])
        increment_permissions_version(perfil.empresa)
        audit_event("profile_deactivate", request, request.user, "perfil_acesso", perfil.pk)
        return Response(self.get_serializer(perfil).data)

    def perform_destroy(self, instance):
        instance.delete()
        increment_permissions_version(instance.empresa)
        audit_event("profile_delete", self.request, self.request.user, "perfil_acesso", instance.pk)

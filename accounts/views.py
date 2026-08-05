from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.http import JsonResponse
from django.db.models import Count, Q
from rest_framework import viewsets, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.views import APIView

from rest_framework.permissions import IsAuthenticated

from django.db import transaction
from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService
from accounts.services.effective_access import EffectiveAccessService, MasterTransferService, ProfileDefaultService, audit_event, increment_permissions_version, sync_legacy_license_flags
from accounts.services.profiles import hidden_profile_names_for_company
from accounts.services.sessions import ConcurrentSessionService
from .permissions import CanManageAccessProfiles, CanManageCompanyUsers
from .serializers import (
    EmpresaContratoSerializer,
    EmpresaModuloSerializer,
    ModuloSistemaSerializer,
    PerfilAcessoSerializer,
    SessaoUsuarioSerializer,
    UserSerializer,
)
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, ModuloSistema
from .models import PerfilAcesso, SessaoUsuario

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
        transaction.on_commit(lambda: AuditService.success(AuditAction.USER_CREATED, category=AuditCategory.USER_MANAGEMENT, request=request, user=request.user, instance=user, metadata={"username": user.username}))
        return Response({"message": "Usuário criado com sucesso.", "user": UserSerializer(user, context={"request": request}).data}, status=201)

# ---- Login (público) → token ----
class TokenLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        raw_token, sessao, user = ConcurrentSessionService.login(
            request,
            request.data.get("username"),
            request.data.get("password"),
            request.data.get("device_id") or request.data.get("dispositivo_id"),
        )
        user._current_access_session = sessao
        return Response({
            "token": raw_token,
            "session_id": str(sessao.session_id),
            "deve_trocar_senha": bool(user.deve_trocar_senha),
            "user": UserSerializer(user).data | EffectiveAccessService(user).session_payload(),
        })

# ---- Logout (autenticado) → revoga todos os tokens do usuário ----
class TokenLogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        sessao = getattr(request, "access_session", None)
        if sessao:
            ConcurrentSessionService.close_session(sessao, "LOGOUT", request.user, request)
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
            scoped = qs
        else:
            empresa_id = getattr(user, "empresa_id", None)
            if empresa_id:
                scoped = qs.filter(empresa_id=empresa_id)
            else:
                return qs.none()
        params = self.request.query_params
        if params.get("type"):
            scoped = scoped.filter(type=params["type"])
        if params.get("ativo") in {"true", "false"}:
            scoped = scoped.filter(is_active=params["ativo"] == "true")
        if params.get("perfil"):
            scoped = scoped.filter(perfil_principal_id=params["perfil"])
        if params.get("loja"):
            scoped = scoped.filter(Q(loja_id=params["loja"]) | Q(lojas__Idloja=params["loja"])).distinct()
        return scoped

    def perform_destroy(self, instance):
        user = self.request.user
        if not user.is_superuser:
            raise PermissionDenied("Usuários de empresas devem ser inativados, não excluídos.")
        if not user.is_superuser and instance.empresa_id != getattr(user, "empresa_id", None):
            raise PermissionDenied("Você só pode excluir usuários da sua empresa.")
        if EffectiveAccessService(instance).is_company_master():
            raise PermissionDenied("Transfira o master antes de excluir este usuário.")
        before = {"username": instance.username, "empresa": instance.empresa_id, "is_active": instance.is_active}
        with transaction.atomic():
            instance_id = instance.pk
            instance.delete()
            # Obrigatório: exclusão administrativa remove acesso da plataforma.
            AuditService.required_success(AuditAction.USER_DELETED, category=AuditCategory.USER_MANAGEMENT, request=self.request, user=user, app_label="accounts", model="user", object_id=instance_id, before=before, after={"deleted": True}, status_code=200)

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        qs = self.get_queryset()
        master_ids = []
        if request.user.is_superuser:
            master_ids = list(EmpresaContrato.objects.exclude(usuario_master_id__isnull=True).values_list("usuario_master_id", flat=True))
        elif getattr(request.user, "empresa_id", None):
            master_ids = list(EmpresaContrato.objects.filter(empresa_id=request.user.empresa_id).exclude(usuario_master_id__isnull=True).values_list("usuario_master_id", flat=True))
        return Response(qs.aggregate(
            total=Count("id"),
            ativos=Count("id", filter=Q(is_active=True)),
            inativos=Count("id", filter=Q(is_active=False)),
            masters=Count("id", filter=Q(id__in=master_ids)),
        ) | {
            "com_sessao_ativa": qs.filter(
                id__in=ConcurrentSessionService.valid_sessions_queryset().values("usuario_id")
            ).distinct().count(),
        })

    @action(detail=True, methods=["get"], url_path="sessoes")
    def sessoes(self, request, pk=None):
        user = self.get_object()
        qs = user.sessoes_acesso.select_related("empresa", "usuario", "usuario__perfil_principal", "loja", "session_token").order_by("-ultima_atividade_em")
        return Response(SessaoUsuarioSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="encerrar-sessoes")
    def encerrar_sessoes(self, request, pk=None):
        with transaction.atomic():
            user = self.get_queryset().select_for_update().get(pk=pk)
            self.check_object_permissions(request, user)
            sessoes = list(ConcurrentSessionService.valid_sessions_queryset().select_for_update().filter(usuario=user))
            for sessao in sessoes:
                ConcurrentSessionService.close_session(sessao, "ADMIN_USER_SESSIONS_CLOSED", request.user, request, audit=False)
            AuditService.required_success(
                AuditAction.USER_SESSIONS_CLOSED,
                category=AuditCategory.USER_MANAGEMENT,
                request=request,
                user=request.user,
                instance=user,
                metadata={"sessoes_encerradas": len(sessoes), "motivo": "ADMIN_USER_SESSIONS_CLOSED"},
                status_code=200,
            )
        return Response({"sessoes_encerradas": len(sessoes)})

    @action(detail=True, methods=["post"], url_path="redefinir-senha")
    def redefinir_senha(self, request, pk=None):
        user = self.get_object()
        nova = request.data.get("nova_senha") or ""
        confirmacao = request.data.get("confirmacao") or ""
        if nova != confirmacao:
            raise ValidationError({"confirmacao": "Confirmação da senha não confere."})
        validate_password(nova, user)
        encerrar = request.data.get("encerrar_sessoes", True)
        with transaction.atomic():
            user = self.get_queryset().select_for_update().get(pk=pk)
            self.check_object_permissions(request, user)
            user.set_password(nova)
            user.deve_trocar_senha = True
            user.save(update_fields=["password", "deve_trocar_senha"])
            sessoes = []
            if encerrar:
                sessoes = list(user.sessoes_acesso.select_for_update().filter(ativa=True))
                for sessao in sessoes:
                    ConcurrentSessionService.close_session(sessao, "PASSWORD_RESET", request.user, request, audit=False)
            AuditService.required_success(
                AuditAction.USER_PASSWORD_RESET,
                category=AuditCategory.USER_MANAGEMENT,
                request=request,
                user=request.user,
                instance=user,
                metadata={"sessoes_encerradas": len(sessoes), "motivo": "PASSWORD_RESET"},
                status_code=200,
            )
        return Response({"detail": "Senha redefinida.", "sessoes_encerradas": len(sessoes)})

    @action(detail=True, methods=["post"], permission_classes=[CanManageCompanyUsers], url_path="ativar")
    def ativar(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active"])
        if user.empresa_id:
            increment_permissions_version(user.empresa)
        transaction.on_commit(lambda: AuditService.success(AuditAction.USER_ACTIVATED, category=AuditCategory.USER_MANAGEMENT, request=request, user=request.user, instance=user, before={"is_active": False}, after={"is_active": True}))
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"], permission_classes=[CanManageCompanyUsers], url_path="inativar")
    def inativar(self, request, pk=None):
        user = self.get_object()
        if EffectiveAccessService(user).is_company_master():
            raise PermissionDenied("Transfira o master antes de inativar este usuário.")
        user.is_active = False
        user.save(update_fields=["is_active"])
        for sessao in user.sessoes_acesso.filter(ativa=True):
            ConcurrentSessionService.close_session(sessao, "USER_INACTIVE", request.user, request)
        if user.empresa_id:
            increment_permissions_version(user.empresa)
        transaction.on_commit(lambda: AuditService.success(AuditAction.USER_INACTIVATED, category=AuditCategory.USER_MANAGEMENT, request=request, user=request.user, instance=user, before={"is_active": True}, after={"is_active": False}))
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
        if hasattr(request, "access_session"):
            request.user._current_access_session = request.access_session
        return Response(UserSerializer(request.user).data | EffectiveAccessService(request.user).session_payload())


class ChangeRequiredPasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        senha_atual = request.data.get("senha_atual") or ""
        nova = request.data.get("nova_senha") or ""
        confirmacao = request.data.get("confirmacao") or ""
        user = request.user
        if not user.check_password(senha_atual):
            raise ValidationError({"senha_atual": "Senha atual inválida."})
        if nova != confirmacao:
            raise ValidationError({"confirmacao": "Confirmação da senha não confere."})
        if senha_atual == nova:
            raise ValidationError({"nova_senha": "A nova senha deve ser diferente da senha atual."})
        validate_password(nova, user)
        current_session = getattr(request, "access_session", None)
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            user.set_password(nova)
            user.deve_trocar_senha = False
            user.save(update_fields=["password", "deve_trocar_senha"])
            sessoes = user.sessoes_acesso.select_for_update().filter(ativa=True)
            if current_session:
                sessoes = sessoes.exclude(pk=current_session.pk)
            sessoes = list(sessoes)
            for sessao in sessoes:
                ConcurrentSessionService.close_session(sessao, "PASSWORD_CHANGED", user, request, audit=False)
            AuditService.required_success(
                AuditAction.USER_PASSWORD_CHANGED,
                category=AuditCategory.USER_MANAGEMENT,
                request=request,
                user=user,
                instance=user,
                metadata={"sessoes_encerradas": len(sessoes), "motivo": "PASSWORD_CHANGED"},
                status_code=200,
            )
        return Response({"detail": "Senha alterada.", "deve_trocar_senha": False, "sessoes_encerradas": len(sessoes)})


class SessaoUsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SessaoUsuario.objects.select_related("empresa", "usuario", "usuario__perfil_principal", "loja", "session_token").all().order_by("-ultima_atividade_em")
    serializer_class = SessaoUsuarioSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        empresa = self.request.query_params.get("empresa")
        ativa = self.request.query_params.get("ativa")
        if not user.is_superuser:
            if not EffectiveAccessService(user).is_company_master():
                qs = qs.filter(usuario=user)
            else:
                qs = qs.filter(empresa_id=user.empresa_id)
        elif empresa:
            qs = qs.filter(empresa_id=empresa)
        if ativa is not None and ativa != "":
            if str(ativa).lower() in {"1", "true", "sim"}:
                qs = qs.filter(pk__in=ConcurrentSessionService.valid_sessions_queryset().values("pk"))
            else:
                qs = qs.exclude(pk__in=ConcurrentSessionService.valid_sessions_queryset().values("pk"))
        return qs

    @action(detail=False, methods=["post"], url_path="heartbeat")
    def heartbeat(self, request):
        sessao = getattr(request, "access_session", None)
        if not sessao or not ConcurrentSessionService.is_session_valid(sessao):
            raise PermissionDenied("Sessão não identificada.")
        ConcurrentSessionService.touch(sessao, force=True)
        permissions_version = None
        if sessao.empresa_id:
            permissions_version = sessao.empresa.contrato.permissions_version
        return Response({
            "session_id": str(sessao.session_id),
            "ativa": sessao.ativa,
            "ultima_atividade_em": sessao.ultima_atividade_em,
            "permissions_version": permissions_version,
        })

    @action(detail=True, methods=["post"], url_path="encerrar")
    def encerrar(self, request, pk=None):
        sessao = self.get_object()
        user = request.user
        allowed = user.is_superuser or sessao.usuario_id == user.id or (
            sessao.empresa_id == getattr(user, "empresa_id", None) and EffectiveAccessService(user).is_company_master()
        )
        if not allowed:
            AuditService.denied(AuditAction.SESSION_CLOSE_DENIED, category=AuditCategory.SECURITY, request=request, user=user, session=sessao, app_label="accounts", model="sessao_usuario", object_id=sessao.pk)
            raise PermissionDenied("Sem permissão para encerrar esta sessão.")
        ConcurrentSessionService.close_session(sessao, "ADMIN_TERMINATED" if sessao.usuario_id != user.id else "SELF_TERMINATED", user, request)
        return Response(self.get_serializer(sessao).data)


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

    def get_serializer_class(self):
        if getattr(self, "action", None) in {"retrieve", "list"}:
            from .serializers import EmpresaContratoDetalheSerializer
            return EmpresaContratoDetalheSerializer
        return super().get_serializer_class()

    def _exigir_superusuario(self):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Somente superusuário pode alterar contrato.")

    def perform_destroy(self, instance):
        self._exigir_superusuario()
        return super().perform_destroy(instance)

    def perform_create(self, serializer):
        self._exigir_superusuario()
        with transaction.atomic():
            obj = serializer.save()
            obj.incrementar_versao()
            sync_legacy_license_flags(obj.empresa)
            # Obrigatório: criação de contrato define limites e módulos efetivos do cliente.
            AuditService.required_success(AuditAction.CONTRACT_CREATED, category=AuditCategory.CONTRACT, request=self.request, user=self.request.user, instance=obj, after={"status": obj.status, "limite_sessoes_simultaneas": obj.limite_sessoes_simultaneas, "plano_completo": obj.plano_completo}, status_code=201)

    def perform_update(self, serializer):
        self._exigir_superusuario()
        before = {"status": serializer.instance.status, "limite_sessoes_simultaneas": serializer.instance.limite_sessoes_simultaneas, "plano_completo": serializer.instance.plano_completo} if serializer.instance else {}
        with transaction.atomic():
            obj = serializer.save()
            obj.incrementar_versao()
            sync_legacy_license_flags(obj.empresa)
            after = {"status": obj.status, "limite_sessoes_simultaneas": obj.limite_sessoes_simultaneas, "plano_completo": obj.plano_completo}
            action = AuditAction.CONTRACT_LIMIT_CHANGED if before.get("limite_sessoes_simultaneas") != after.get("limite_sessoes_simultaneas") else AuditAction.CONTRACT_STATUS_CHANGED if before.get("status") != after.get("status") else AuditAction.CONTRACT_UPDATED
            # Obrigatório: alteração de contrato pode liberar ou bloquear acesso do cliente.
            AuditService.required_success(action, category=AuditCategory.CONTRACT, request=self.request, user=self.request.user, instance=obj, before=before, after=after, status_code=200)

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
        with transaction.atomic():
            obj = serializer.save()
            increment_permissions_version(obj.empresa)
            sync_legacy_license_flags(obj.empresa)
            # Obrigatório: módulos contratados determinam permissões efetivas do cliente.
            AuditService.required_success(AuditAction.CONTRACT_UPDATED, category=AuditCategory.CONTRACT, request=self.request, user=self.request.user, instance=obj, after={"modulo": obj.modulo.chave, "contratado": obj.contratado}, status_code=201)

    def perform_update(self, serializer):
        old = serializer.instance.contratado if serializer.instance else None
        with transaction.atomic():
            obj = serializer.save()
            increment_permissions_version(obj.empresa)
            sync_legacy_license_flags(obj.empresa)
            # Obrigatório: alteração de módulo contratado muda acesso disponível.
            AuditService.required_success(AuditAction.CONTRACT_UPDATED, category=AuditCategory.CONTRACT, request=self.request, user=self.request.user, instance=obj, before={"contratado": old}, after={"contratado": obj.contratado, "modulo": obj.modulo.chave}, status_code=200)


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
        hidden_names = hidden_profile_names_for_company(self.request.user.empresa)
        return qs.filter(empresa_id=empresa_id).exclude(nome__in=hidden_names)

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
        transaction.on_commit(lambda: AuditService.success(AuditAction.PROFILE_UPDATED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=perfil, before={"ativo": False}, after={"ativo": True}))
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
        transaction.on_commit(lambda: AuditService.success(AuditAction.PROFILE_INACTIVATED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=perfil, before={"ativo": True}, after={"ativo": False}))
        return Response(self.get_serializer(perfil).data)

    def perform_destroy(self, instance):
        before = {"nome": instance.nome, "empresa": instance.empresa_id, "ativo": instance.ativo}
        instance.delete()
        increment_permissions_version(instance.empresa)
        transaction.on_commit(lambda: AuditService.success(AuditAction.PROFILE_INACTIVATED, category=AuditCategory.ACCESS, request=self.request, user=self.request.user, app_label="accounts", model="perfil_acesso", object_id=instance.pk, before=before, after={"deleted": True}))

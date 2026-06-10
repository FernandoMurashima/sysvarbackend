from django.contrib.auth import get_user_model
from django.http import JsonResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.serializers import AuthTokenSerializer

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from auditoria.models import AuditLog
from .serializers import TIPOS_EXIGEM_LOJA, UserSerializer

User = get_user_model()

# ---- Health (público) ----
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health(request):
    return JsonResponse({"status": "ok", "app": "sysvar2"})

# ---- Register (público) ----
class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        email = (data.get("email") or "").strip()
        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        user_type = (data.get("type") or "Regular").strip()

        if not username or not password:
            return Response({"error": "username e password são obrigatórios."}, status=400)

        allowed_types = {
            "Regular",
            "Vendedor",
            "Caixa",
            "Gerente",
            "Diretor",
            "Admin",
            "Auxiliar",
            "Assistente",
            "AssistenteReceber",
            "AssistentePagar",
        }
        if user_type not in allowed_types:
            user_type = "Regular"

        if User.objects.filter(username=username).exists():
            return Response({"error": "username já existe."}, status=400)

        loja_key = data.get("Idloja") or data.get("loja") or data.get("loja_id")
        empresa_key = data.get("Idempresa") or data.get("empresa") or data.get("empresa_id")
        if user_type in TIPOS_EXIGEM_LOJA and not loja_key:
            return Response({"Idloja": ["Vincule este usuário a uma filial ou matriz."]}, status=400)

        user = User(
            username=username,
            email=email or None,
            first_name=first_name,
            last_name=last_name,
        )
        # se seu User tiver campo 'type', define:
        try:
            setattr(user, "type", user_type)
        except Exception:
            pass

        user.set_password(password)
        user.save()

        # vincular loja, se enviada
        if loja_key:
            try:
                from cadastros.models import Loja  # evita import circular
                loja = Loja.objects.get(pk=int(loja_key))
                user.loja = loja
                user.empresa = loja.empresa
                user.save(update_fields=["loja", "empresa"])
            except Exception:
                pass
        elif empresa_key:
            try:
                from cadastros.models import Empresa
                user.empresa = Empresa.objects.get(pk=int(empresa_key))
                user.save(update_fields=["empresa"])
            except Exception:
                pass

        token, _ = Token.objects.get_or_create(user=user)

        # auditoria (login não, apenas criação de usuário)
        try:
            AuditLog.objects.create(
                action="create",
                app_label="accounts",
                model="user",
                object_id=str(user.pk),
                changes={"username": user.username, "type": getattr(user, "type", None)},
                user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
                ip=(request.META.get("REMOTE_ADDR") or "")[:45],
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
            )
        except Exception:
            pass

        return Response(
            {
                "message": "Usuário criado com sucesso.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "type": getattr(user, "type", "Regular"),
                    "Idempresa": getattr(user, "empresa_id", None),
                    "empresa_nome": getattr(getattr(user, "empresa", None), "nome", None),
                    "Idloja": getattr(user, "loja_id", None),
                    "loja_nome": getattr(getattr(user, "loja", None), "nome_loja", None),
                },
                "token": token.key,
            },
            status=201,
        )

# ---- Login (público) → token ----
class TokenLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AuthTokenSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
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

        return Response({"token": token.key, "user": UserSerializer(user).data})

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
    queryset = User.objects.all().order_by("id")
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["id", "username", "date_joined"]

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

class UserMeView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response(UserSerializer(request.user).data)

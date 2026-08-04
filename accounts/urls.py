from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EmpresaContratoViewSet,
    EmpresaModuloViewSet,
    ModuloSistemaViewSet,
    PerfilAcessoViewSet,
    SessaoUsuarioViewSet,
    UserViewSet,
    TokenLoginView,
    TokenLogoutView,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="users")
router.register(r"modulos", ModuloSistemaViewSet, basename="modulos")
router.register(r"contratos", EmpresaContratoViewSet, basename="contratos")
router.register(r"empresa-modulos", EmpresaModuloViewSet, basename="empresa-modulos")
router.register(r"perfis", PerfilAcessoViewSet, basename="perfis")
router.register(r"sessoes", SessaoUsuarioViewSet, basename="sessoes")

urlpatterns = [
    path("", include(router.urls)),
    # NOVO: login/logout por token sob /api/accounts/...
    path("auth/token/", TokenLoginView.as_view(), name="accounts_token_login"),
    path("auth/logout/", TokenLogoutView.as_view(), name="accounts_token_logout"),
]

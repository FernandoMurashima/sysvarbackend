from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet, TokenLoginView, TokenLogoutView

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="users")

urlpatterns = [
    path("", include(router.urls)),
    # NOVO: login/logout por token sob /api/accounts/...
    path("auth/token/", TokenLoginView.as_view(), name="accounts_token_login"),
    path("auth/logout/", TokenLogoutView.as_view(), name="accounts_token_logout"),
]

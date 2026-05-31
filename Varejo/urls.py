from django.contrib import admin
from django.urls import path, include, re_path
from django.http import JsonResponse
from django.db import connection
from django.utils.timezone import now

# drf-yasg (Swagger/Redoc)
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions

# trocamos o import: usamos nosso login/logout
from accounts.views import TokenLoginView, TokenLogoutView, UserMeView

schema_view = get_schema_view(
    openapi.Info(
        title="Sysvar API",
        default_version="v1",
        description="Documentacao interativa da API Sysvar",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

def health_view(_request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return JsonResponse(
            {"status": "ok", "time": now().isoformat(), "checks": {"database": True}}
        )
    except Exception as e:
        return JsonResponse(
            {
                "status": "error",
                "time": now().isoformat(),
                "checks": {"database": False},
                "error": str(e),
            },
            status=500,
        )

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health_view),
    path("api/me/", UserMeView.as_view(), name="api_me"),

    # >>> compat: mesmo endpoint de antes, agora auditado
    path("api/auth/token/", TokenLoginView.as_view(), name="api_token_auth"),
    path("api/auth/logout/", TokenLogoutView.as_view(), name="api_token_logout"),

    path("api/cadastros/", include("cadastros.urls")),
    path("api/accounts/", include("accounts.urls")),
    path("api/auditoria/", include("auditoria.urls")),
    path("api/produto/", include("produto.urls")),
    path('api/financeiro/', include('financeiro.urls')),
    path('api/compras/', include('compras.urls')),
    path('api/fiscal/', include('fiscal.urls')),

    # Documentacao (Swagger/Redoc)
    re_path(r"^api/schema(?P<format>\.json|\.yaml)$", schema_view.without_ui(cache_timeout=0), name="schema-json"),
    path("api/docs/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("api/redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
]

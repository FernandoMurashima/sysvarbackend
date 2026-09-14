from django.urls import path

from hub.views import (
    HubAtivacaoAdminView,
    HubAtivarView,
    HubBootstrapView,
    HubCatalogoView,
    HubFormasPagamentoView,
    HubHeartbeatView,
    HubOperadoresView,
)

urlpatterns = [
    path("ativacoes/", HubAtivacaoAdminView.as_view(), name="hub-ativacoes"),
    path("ativar/", HubAtivarView.as_view(), name="hub-ativar"),
    path("bootstrap/", HubBootstrapView.as_view(), name="hub-bootstrap"),
    path("catalogo/", HubCatalogoView.as_view(), name="hub-catalogo"),
    path("formas-pagamento/", HubFormasPagamentoView.as_view(), name="hub-formas-pagamento"),
    path("heartbeat/", HubHeartbeatView.as_view(), name="hub-heartbeat"),
    path("operadores/", HubOperadoresView.as_view(), name="hub-operadores"),
]

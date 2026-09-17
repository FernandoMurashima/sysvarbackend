from django.urls import path

from hub.views import (
    HubAtivacaoAdminView,
    HubAtivarView,
    HubBootstrapView,
    HubCatalogoView,
    HubClientesView,
    HubFormasPagamentoView,
    HubHeartbeatView,
    HubOperadoresView,
    HubSyncPushView,
    HubTiposDespesaPdvView,
    HubVendedoresView,
)

urlpatterns = [
    path("ativacoes/", HubAtivacaoAdminView.as_view(), name="hub-ativacoes"),
    path("ativar/", HubAtivarView.as_view(), name="hub-ativar"),
    path("bootstrap/", HubBootstrapView.as_view(), name="hub-bootstrap"),
    path("catalogo/", HubCatalogoView.as_view(), name="hub-catalogo"),
    path("clientes/", HubClientesView.as_view(), name="hub-clientes"),
    path("formas-pagamento/", HubFormasPagamentoView.as_view(), name="hub-formas-pagamento"),
    path("heartbeat/", HubHeartbeatView.as_view(), name="hub-heartbeat"),
    path("sync/push/", HubSyncPushView.as_view(), name="hub-sync-push"),
    path("operadores/", HubOperadoresView.as_view(), name="hub-operadores"),
    path("tipos-despesa-pdv/", HubTiposDespesaPdvView.as_view(), name="hub-tipos-despesa-pdv"),
    path("vendedores/", HubVendedoresView.as_view(), name="hub-vendedores"),
]

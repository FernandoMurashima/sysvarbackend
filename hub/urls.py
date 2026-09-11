from django.urls import path

from hub.views import HubAtivacaoAdminView, HubAtivarView, HubBootstrapView, HubHeartbeatView

urlpatterns = [
    path("ativacoes/", HubAtivacaoAdminView.as_view(), name="hub-ativacoes"),
    path("ativar/", HubAtivarView.as_view(), name="hub-ativar"),
    path("bootstrap/", HubBootstrapView.as_view(), name="hub-bootstrap"),
    path("heartbeat/", HubHeartbeatView.as_view(), name="hub-heartbeat"),
]

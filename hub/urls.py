from django.urls import path

from hub.views import HubAtivacaoAdminView, HubAtivarView, HubHeartbeatView

urlpatterns = [
    path("ativacoes/", HubAtivacaoAdminView.as_view(), name="hub-ativacoes"),
    path("ativar/", HubAtivarView.as_view(), name="hub-ativar"),
    path("heartbeat/", HubHeartbeatView.as_view(), name="hub-heartbeat"),
]

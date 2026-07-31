from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DistribuicaoViewSet,
    MercadoriaTransitoViewSet,
    PedidoVendaDistribuicaoViewSet,
    PerfilDistribuicaoItemViewSet,
    PerfilDistribuicaoViewSet,
)

router = DefaultRouter()
router.register("perfis", PerfilDistribuicaoViewSet, basename="distribuicao-perfis")
router.register("perfis-itens", PerfilDistribuicaoItemViewSet, basename="distribuicao-perfis-itens")
router.register("distribuicoes", DistribuicaoViewSet, basename="distribuicao-distribuicoes")
router.register("pedidos-venda", PedidoVendaDistribuicaoViewSet, basename="distribuicao-pedidos-venda")
router.register("transitos", MercadoriaTransitoViewSet, basename="distribuicao-transitos")

urlpatterns = [path("", include(router.urls))]

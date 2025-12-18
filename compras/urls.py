from rest_framework.routers import DefaultRouter
from .views import (
    PedidoCompraViewSet, PedidoCompraItemViewSet,
    PedidoCompraEntregaViewSet, PedidoCompraParcelaViewSet
)

router = DefaultRouter()
router.register('pedidos', PedidoCompraViewSet)
router.register('itens', PedidoCompraItemViewSet)
router.register('entregas', PedidoCompraEntregaViewSet)
router.register('parcelas', PedidoCompraParcelaViewSet)

urlpatterns = router.urls

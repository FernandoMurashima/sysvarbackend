from rest_framework.routers import DefaultRouter
from .views import (
    PedidoCompraViewSet, PedidoCompraItemViewSet,
    PedidoCompraEntregaViewSet, PedidoCompraParcelaViewSet,
    RequisicaoHistoricoViewSet, RequisicaoItemViewSet,
    RequisicaoFinalidadeAquisicaoViewSet, RequisicaoMaterialCategoriaViewSet,
    RequisicaoServicoCategoriaViewSet, RequisicaoSetorViewSet, RequisicaoViewSet,
)

router = DefaultRouter()
router.register('pedidos', PedidoCompraViewSet)
router.register('itens', PedidoCompraItemViewSet)
router.register('entregas', PedidoCompraEntregaViewSet)
router.register('parcelas', PedidoCompraParcelaViewSet)
router.register('requisicoes', RequisicaoViewSet)
router.register('requisicao-itens', RequisicaoItemViewSet)
router.register('requisicao-historico', RequisicaoHistoricoViewSet)
router.register('requisicao-servico-categorias', RequisicaoServicoCategoriaViewSet)
router.register('requisicao-setores', RequisicaoSetorViewSet)
router.register('requisicao-material-categorias', RequisicaoMaterialCategoriaViewSet)
router.register('requisicao-finalidades-aquisicao', RequisicaoFinalidadeAquisicaoViewSet)

urlpatterns = router.urls

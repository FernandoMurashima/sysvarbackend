from rest_framework.routers import DefaultRouter
from .views import (
    CotacaoViewSet,
    CotacaoFornecedorViewSet,
    CotacaoItemViewSet,
    CotacaoPropostaViewSet,
    OrdemServicoViewSet,
    PedidoCompraViewSet, PedidoCompraItemViewSet,
    PedidoCompraEntregaViewSet, PedidoCompraParcelaViewSet,
    RequisicaoHistoricoViewSet, RequisicaoItemViewSet,
    RequisicaoFinalidadeAquisicaoViewSet, RequisicaoMaterialCategoriaViewSet,
    RequisicaoMatrizResponsabilidadeViewSet, RequisicaoServicoCategoriaViewSet,
    RequisicaoSetorViewSet, RequisicaoViewSet,
)

router = DefaultRouter()
router.register('cotacoes', CotacaoViewSet)
router.register('cotacao-fornecedores', CotacaoFornecedorViewSet)
router.register('cotacao-itens', CotacaoItemViewSet)
router.register('cotacao-propostas', CotacaoPropostaViewSet)
router.register('pedidos', PedidoCompraViewSet)
router.register('ordens-servico', OrdemServicoViewSet)
router.register('itens', PedidoCompraItemViewSet)
router.register('entregas', PedidoCompraEntregaViewSet)
router.register('parcelas', PedidoCompraParcelaViewSet)
router.register('requisicoes', RequisicaoViewSet)
router.register('requisicao-itens', RequisicaoItemViewSet)
router.register('requisicao-historico', RequisicaoHistoricoViewSet)
router.register('requisicao-servico-categorias', RequisicaoServicoCategoriaViewSet)
router.register('requisicao-setores', RequisicaoSetorViewSet)
router.register('requisicao-matriz-responsabilidade', RequisicaoMatrizResponsabilidadeViewSet)
router.register('requisicao-material-categorias', RequisicaoMaterialCategoriaViewSet)
router.register('requisicao-finalidades-aquisicao', RequisicaoFinalidadeAquisicaoViewSet)

urlpatterns = router.urls

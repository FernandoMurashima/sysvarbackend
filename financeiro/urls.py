from rest_framework.routers import DefaultRouter
from .views import (
    CaixaViewSet, ContaBancariaViewSet, MovimentacaoFinanceiraViewSet,
    CashbackConfigViewSet, CashbackMovimentoViewSet,
    PagarViewSet, PagarItemViewSet, PagarRateioViewSet,
    ReceberViewSet, ReceberItemViewSet, ReceberRateioViewSet,
    FormaPagamentoViewSet, FormaPagamentoParcelaViewSet
)

router = DefaultRouter()
router.register('formas', FormaPagamentoViewSet)
router.register('formas-parcelas', FormaPagamentoParcelaViewSet)
router.register('cashback-config', CashbackConfigViewSet)
router.register('cashback-movimentos', CashbackMovimentoViewSet)
router.register('caixas', CaixaViewSet)
router.register('contas-bancarias', ContaBancariaViewSet)
router.register('movimentacoes', MovimentacaoFinanceiraViewSet)
router.register('pagar', PagarViewSet)
router.register('pagar-item', PagarItemViewSet)
router.register('pagar-rateio', PagarRateioViewSet)
router.register('receber', ReceberViewSet)
router.register('receber-item', ReceberItemViewSet)
router.register('receber-rateio', ReceberRateioViewSet)

urlpatterns = router.urls

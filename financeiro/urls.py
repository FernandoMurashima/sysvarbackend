from rest_framework.routers import DefaultRouter
from .views import (
    ConfigFinanceiraViewSet, TipoDespesaPdvViewSet,
    CaixaViewSet, ContaBancariaViewSet, MovimentacaoFinanceiraViewSet, LancamentoContabilViewSet,
    CashbackConfigViewSet, CashbackMovimentoViewSet,
    ValeTrocaViewSet, ValeTrocaMovimentoViewSet,
    PagarViewSet, PagarItemViewSet, PagarRateioViewSet,
    ReceberViewSet, ReceberItemViewSet, ReceberRateioViewSet,
    AntecipacaoRecebivelViewSet,
    FormaPagamentoViewSet, FormaPagamentoParcelaViewSet,
    PrazoPagamentoViewSet, PrazoPagamentoParcelaViewSet
)

router = DefaultRouter()
router.register('config-financeira', ConfigFinanceiraViewSet)
router.register('tipos-despesa-pdv', TipoDespesaPdvViewSet)
router.register('formas', FormaPagamentoViewSet)
router.register('formas-parcelas', FormaPagamentoParcelaViewSet)
router.register('prazos', PrazoPagamentoViewSet)
router.register('prazos-parcelas', PrazoPagamentoParcelaViewSet)
router.register('cashback-config', CashbackConfigViewSet)
router.register('cashback-movimentos', CashbackMovimentoViewSet)
router.register('vales-troca', ValeTrocaViewSet)
router.register('vales-troca-movimentos', ValeTrocaMovimentoViewSet)
router.register('caixas', CaixaViewSet)
router.register('contas-bancarias', ContaBancariaViewSet)
router.register('movimentacoes', MovimentacaoFinanceiraViewSet)
router.register('lancamentos-contabeis', LancamentoContabilViewSet)
router.register('pagar', PagarViewSet)
router.register('pagar-item', PagarItemViewSet)
router.register('pagar-rateio', PagarRateioViewSet)
router.register('receber', ReceberViewSet)
router.register('receber-item', ReceberItemViewSet)
router.register('receber-rateio', ReceberRateioViewSet)
router.register('antecipacoes-recebiveis', AntecipacaoRecebivelViewSet)

urlpatterns = router.urls

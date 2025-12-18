from rest_framework.routers import DefaultRouter
from .views import (
    PagarViewSet, PagarItemViewSet, PagarRateioViewSet,
    FormaPagamentoViewSet, FormaPagamentoParcelaViewSet
)

router = DefaultRouter()
router.register('formas', FormaPagamentoViewSet)
router.register('formas-parcelas', FormaPagamentoParcelaViewSet)
router.register('pagar', PagarViewSet)
router.register('pagar-item', PagarItemViewSet)
router.register('pagar-rateio', PagarRateioViewSet)

urlpatterns = router.urls

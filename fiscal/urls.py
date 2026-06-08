from rest_framework.routers import DefaultRouter

from .views import NFCeViewSet, NotaFiscalEntradaItemViewSet, NotaFiscalEntradaViewSet, VendaDevolucaoViewSet, VendaPdvViewSet


router = DefaultRouter()
router.register("notas-entrada", NotaFiscalEntradaViewSet)
router.register("notas-entrada-itens", NotaFiscalEntradaItemViewSet)
router.register("vendas-pdv", VendaPdvViewSet)
router.register("devolucoes-venda", VendaDevolucaoViewSet)
router.register("nfce", NFCeViewSet)

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import CfopViewSet, NFCeViewSet, NotaFiscalEntradaItemViewSet, NotaFiscalEntradaViewSet, NotaFiscalSaidaItemViewSet, NotaFiscalSaidaViewSet, RegraTributariaViewSet, TributoViewSet, VendaDevolucaoViewSet, VendaPdvViewSet


router = DefaultRouter()
router.register("cfop", CfopViewSet)
router.register("tributos", TributoViewSet)
router.register("regras-tributarias", RegraTributariaViewSet)
router.register("notas-entrada", NotaFiscalEntradaViewSet)
router.register("notas-entrada-itens", NotaFiscalEntradaItemViewSet)
router.register("notas-saida", NotaFiscalSaidaViewSet)
router.register("notas-saida-itens", NotaFiscalSaidaItemViewSet)
router.register("vendas-pdv", VendaPdvViewSet)
router.register("devolucoes-venda", VendaDevolucaoViewSet)
router.register("nfce", NFCeViewSet)

urlpatterns = router.urls

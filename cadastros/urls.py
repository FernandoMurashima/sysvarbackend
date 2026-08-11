from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import EmpresaViewSet, LojaViewSet, CargoViewSet, ClienteViewSet, FornecedorViewSet, FuncionariosViewSet, NatLancamentoViewSet, PlanoContabilViewSet

router = DefaultRouter()
router.register(r'empresas', EmpresaViewSet, basename='empresas')
router.register(r'lojas', LojaViewSet, basename='lojas')
router.register(r'cargos', CargoViewSet, basename='cargos')
router.register(r'clientes', ClienteViewSet, basename='clientes')
router.register(r'fornecedores', FornecedorViewSet, basename='fornecedores')
router.register(r'funcionarios', FuncionariosViewSet, basename='funcionarios')
router.register(r"nat_lancamento", NatLancamentoViewSet, basename="nat_lancamento")
router.register(r"plano-contabil", PlanoContabilViewSet, basename="plano-contabil")

urlpatterns = [
    path('', include(router.urls)),
]

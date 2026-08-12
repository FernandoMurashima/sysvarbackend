from rest_framework.routers import DefaultRouter
from .views import (
    ConfigEanViewSet, NcmViewSet, GradeViewSet, TamanhoViewSet, CorViewSet, MaterialViewSet,
    ColecaoViewSet, UnidadeViewSet, GrupoViewSet, SubgrupoViewSet, TabelaprecoViewSet,
    CodigosViewSet, ProdutoViewSet, ProdutoDetalheViewSet, ProdutoImagemViewSet, TabelaprecoProdutoViewSet, PromocaoViewSet,
    FichaTecnicaViewSet, FichaTecnicaItemViewSet, OrdemProducaoViewSet, OrdemProducaoItemViewSet,
    PackViewSet, PackItemViewSet, EstoqueViewSet, EstoqueMovimentacaoViewSet,
    InventarioEstoqueViewSet, InventarioEstoqueItemViewSet
)

router = DefaultRouter()
router.register('config-ean', ConfigEanViewSet)
router.register('ncm', NcmViewSet)
router.register('grade', GradeViewSet)
router.register('tamanho', TamanhoViewSet)
router.register('cor', CorViewSet)
router.register('material', MaterialViewSet)
router.register('colecao', ColecaoViewSet)
router.register('unidade', UnidadeViewSet)
router.register('grupo', GrupoViewSet)
router.register('subgrupo', SubgrupoViewSet)
router.register('tabela-preco', TabelaprecoViewSet)
router.register('codigos', CodigosViewSet)

router.register('produto', ProdutoViewSet)
router.register('produto-detalhe', ProdutoDetalheViewSet)
router.register('produto-imagem', ProdutoImagemViewSet)
router.register('produto-preco', TabelaprecoProdutoViewSet)
router.register('ficha-tecnica', FichaTecnicaViewSet)
router.register('ficha-tecnica-item', FichaTecnicaItemViewSet)
router.register('ordem-producao', OrdemProducaoViewSet)
router.register('ordem-producao-item', OrdemProducaoItemViewSet)
router.register('promocao', PromocaoViewSet)

router.register('pack', PackViewSet)
router.register('pack-item', PackItemViewSet)
router.register('estoque', EstoqueViewSet)
router.register('estoque-movimentacao', EstoqueMovimentacaoViewSet)
router.register('inventario-estoque', InventarioEstoqueViewSet)
router.register('inventario-estoque-item', InventarioEstoqueItemViewSet)

urlpatterns = router.urls

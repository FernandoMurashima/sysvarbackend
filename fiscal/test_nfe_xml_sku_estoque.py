from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from fiscal.views.nota_fiscal_entrada_sku import NotaFiscalEntradaViewSet
from produto.models import ProdutoDetalhe


class NotaFiscalEntradaXmlSkuEstoqueTests(SimpleTestCase):
    def setUp(self):
        self.view = NotaFiscalEntradaViewSet()

    @patch("fiscal.views.nota_fiscal_entrada_sku.ProdutoDetalhe.objects.only")
    def test_produto_revenda_usa_gtin_do_item_para_identificar_sku(self, only_mock):
        produto = SimpleNamespace(pk=1510, tipo_produto="1", referencia="27-01-01003")
        item = SimpleNamespace(
            numero_item=1,
            produto=produto,
            gtin_ean="7892701000310",
        )
        only_mock.return_value.get.return_value = SimpleNamespace(ean13="7892701000310")

        codigo = self.view._codigo_estoque_item_xml(item)

        self.assertEqual(codigo, "7892701000310")
        only_mock.return_value.get.assert_called_once_with(
            produto_id=1510,
            ean13="7892701000310",
            ativo=True,
        )

    def test_produto_revenda_sem_gtin_bloqueia_efetivacao(self):
        produto = SimpleNamespace(pk=1510, tipo_produto="1", referencia="27-01-01003")
        item = SimpleNamespace(numero_item=1, produto=produto, gtin_ean="")

        with self.assertRaisesMessage(ValueError, "não possui GTIN/EAN"):
            self.view._codigo_estoque_item_xml(item)

    @patch("fiscal.views.nota_fiscal_entrada_sku.ProdutoDetalhe.objects.only")
    def test_gtin_que_nao_pertence_ao_produto_bloqueia_efetivacao(self, only_mock):
        produto = SimpleNamespace(pk=1510, tipo_produto="1", referencia="27-01-01003")
        item = SimpleNamespace(
            numero_item=1,
            produto=produto,
            gtin_ean="7892701000310",
        )
        only_mock.return_value.get.side_effect = ProdutoDetalhe.DoesNotExist

        with self.assertRaisesMessage(ValueError, "não pertence ao produto conciliado"):
            self.view._codigo_estoque_item_xml(item)

    @patch("fiscal.views.nota_fiscal_entrada_sku.EstoqueMovimentacao.objects.filter")
    def test_cancelamento_reutiliza_codigo_real_da_movimentacao_de_entrada(self, filter_mock):
        nota = SimpleNamespace(pk=99)
        item = SimpleNamespace(pk=321)
        movimento = SimpleNamespace(CodigodeBarra="7892701000310")
        filter_mock.return_value.order_by.return_value.first.return_value = movimento

        codigo = self.view._codigo_estoque_cancelamento_xml(nota, item)

        self.assertEqual(codigo, "7892701000310")
        filter_mock.assert_called_once_with(
            documento="NFE:99:ENTRADA",
            tipo="ENTRADA",
            observacao__contains=";ITEM:321",
        )

    def test_produto_nao_revenda_preserva_codigo_legado(self):
        produto = SimpleNamespace(pk=77, tipo_produto="4", referencia="INSUMO-77")
        item = SimpleNamespace(numero_item=1, produto=produto, gtin_ean="7892701000006")
        self.view._codigo_estoque_produto = MagicMock(return_value="2900000000077")

        codigo = self.view._codigo_estoque_item_xml(item)

        self.assertEqual(codigo, "2900000000077")
        self.view._codigo_estoque_produto.assert_called_once_with(produto)

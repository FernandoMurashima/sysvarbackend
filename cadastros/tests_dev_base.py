from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.db.models import Count

from cadastros.models import Empresa, Fornecedor, FornecedorCategoria, FornecedorContato, FornecedorEndereco, Loja
from compras.models import Cotacao, PedidoCompra, Requisicao
from distribuicao.models import Distribuicao, MercadoriaTransito, PerfilDistribuicao, PerfilDistribuicaoItem
from financeiro.models import MovimentacaoFinanceira, Pagar, Receber
from fiscal.models.nota_fiscal_entrada import NotaFiscalEntrada
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida
from fiscal.models.venda_pdv import VendaPdv
from produto.models import ConfigEan, Estoque, EstoqueMovimentacao, FichaTecnica, FichaTecnicaItem, Produto, ProdutoDetalhe, ProdutoFornecedor, Promocao
from sysvar_devtools.dev_base import SysvarDevBaseService


class SysvarDevBaseTests(TransactionTestCase):
    def test_reset_bloqueia_ambiente_producao(self):
        service = SysvarDevBaseService()
        with override_settings(DEBUG=False, DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "sysvar_prod"}}):
            with self.assertRaises(CommandError):
                service.assert_not_production(destructive=True)

    def test_reset_carrega_jsons_e_valida(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)
        self.assertEqual(Empresa.objects.count(), 1)
        self.assertEqual(Loja.objects.count(), 4)
        self.assertEqual(get_user_model().objects.count(), 8)
        self.assertEqual(Fornecedor.objects.count(), 45)
        self.assertEqual(FornecedorCategoria.objects.count(), 45)
        self.assertEqual(FornecedorContato.objects.count(), 65)
        self.assertEqual(FornecedorEndereco.objects.count(), 65)
        self.assertEqual(Produto.objects.count(), 271)
        self.assertEqual(ProdutoDetalhe.objects.count(), 1480)
        self.assertEqual(ProdutoFornecedor.objects.count(), 192)
        self.assertEqual(FichaTecnica.objects.count(), 45)
        self.assertEqual(FichaTecnicaItem.objects.count(), 167)
        self.assertEqual(Promocao.objects.count(), 0)

    def test_reset_idempotente_e_sem_operacional(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        first = SysvarDevBaseService().validate().created
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        second = SysvarDevBaseService().validate().created
        self.assertEqual(first, second)
        for model in [Estoque, EstoqueMovimentacao, Requisicao, Cotacao, PedidoCompra, Distribuicao, MercadoriaTransito, MovimentacaoFinanceira, Pagar, Receber, NotaFiscalEntrada, NotaFiscalSaida, VendaPdv]:
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_ean_e_distribuicao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        self.assertEqual(ProdutoDetalhe.objects.exclude(ean13="").count(), 1480)
        self.assertFalse(ProdutoDetalhe.objects.values("ean13").annotate(c=Count("ean13")).filter(c__gt=1).exists())
        self.assertEqual(ConfigEan.objects.get().next_itemref, 1481)
        self.assertEqual(PerfilDistribuicao.objects.count(), 2)
        self.assertEqual(PerfilDistribuicaoItem.objects.count(), 6)

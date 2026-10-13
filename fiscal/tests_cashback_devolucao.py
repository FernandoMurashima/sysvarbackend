from decimal import Decimal

from django.test import TestCase
from django.db.models import Sum

from cadastros.models import Cliente, Empresa, Funcionarios, Loja
from financeiro.models import CashbackMovimento, ValeTroca
from financeiro.services import restaurar_cashback_utilizado_devolucao
from fiscal.models import VendaDevolucao, VendaDevolucaoItem, VendaPdv, VendaPdvItem
from fiscal.views.venda_pdv import VendaDevolucaoViewSet
from produto.models import ConfigEan, Cor, Grade, Produto, ProdutoDetalhe, Tamanho, Unidade


class CashbackDevolucaoRestituicaoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Cashback Dev", documento="11222333000181")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Cashback Dev", apelido_loja="CBD", cnpj="11222333000181", estado="SP")
        self.cliente = Cliente.objects.create(empresa=self.empresa, tipo_pessoa="PF", documento="39053344705", cpf="39053344705", nome_cliente="Cliente Cashback")
        self.vendedor = Funcionarios.objects.create(empresa=self.empresa, idloja=self.loja, nomefuncionario="Vendedor", cpf="52998224725", matricula="000001")
        unidade = Unidade.objects.create(empresa=self.empresa, Codigo="UN", Descricao="Unidade")
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="M", Descricao="M")
        ConfigEan.objects.create(empresa=self.empresa, company_prefix="7234")
        self.produto = Produto.objects.create(empresa=self.empresa, referencia="REF-CBK", descricao="Produto Cashback", unidade=unidade, ncm="6204.62.00", origem_mercadoria=0, cfop_venda_dentro="5102")
        self.sku = ProdutoDetalhe.objects.create(produto=self.produto, idcor=cor, idtamanho=tamanho)

    def _venda(self, total="100.00", quantidade=1):
        venda = VendaPdv.objects.create(
            empresa=self.empresa,
            loja=self.loja,
            cliente=self.cliente,
            vendedor=self.vendedor,
            documento=f"VE-CBK-{VendaPdv.objects.count() + 1}",
            forma_pagamento="MISTA",
            subtotal=Decimal(total),
            total=Decimal(total),
            valor_recebido=Decimal(total),
        )
        item = VendaPdvItem.objects.create(
            venda=venda,
            produto=self.produto,
            sku=self.sku,
            ean=self.sku.ean13,
            referencia=self.produto.referencia,
            descricao=self.produto.descricao,
            quantidade=quantidade,
            preco_unitario=Decimal(total) / Decimal(quantidade),
            desconto=Decimal("0.00"),
        )
        return venda, item

    def _uso_cashback(self, venda, valor):
        return CashbackMovimento.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            venda_uso=venda,
            tipo=CashbackMovimento.TIPO_DEBITO,
            valor=Decimal(valor),
            observacao=f"Uso na venda {venda.documento}",
        )

    def _devolucao(self, venda, itens, documento=None):
        subtotal = sum((Decimal(valor) for _item, _quantidade, valor in itens), Decimal("0.00"))
        devolucao = VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=venda,
            loja=self.loja,
            cliente=self.cliente,
            documento=documento or f"DEV-CBK-{VendaDevolucao.objects.count() + 1}",
            subtotal=subtotal,
            credito_cliente=subtotal,
        )
        for item, quantidade, valor in itens:
            VendaDevolucaoItem.objects.create(
                devolucao=devolucao,
                venda_item=item,
                produto=item.produto,
                sku=item.sku,
                ean=item.ean,
                referencia=item.referencia,
                descricao=item.descricao,
                quantidade=quantidade,
                preco_unitario=Decimal(valor) / Decimal(quantidade),
                desconto=Decimal("0.00"),
            )
        return devolucao

    def test_devolucao_total_restaura_cashback_e_reduz_vale_troca(self):
        venda, item = self._venda("100.00")
        self._uso_cashback(venda, "30.00")
        devolucao = self._devolucao(venda, [(item, 1, "100.00")])

        restaurado = restaurar_cashback_utilizado_devolucao(devolucao)
        devolucao.refresh_from_db()
        VendaDevolucaoViewSet()._registrar_credito_cliente(devolucao)

        self.assertEqual(restaurado, Decimal("30.00"))
        self.assertEqual(devolucao.credito_cliente, Decimal("70.00"))
        self.assertEqual(ValeTroca.objects.get(devolucao=devolucao).saldo, Decimal("70.00"))

    def test_devolucao_parcial_restitui_cashback_proporcional(self):
        venda, item = self._venda("100.00", quantidade=2)
        self._uso_cashback(venda, "30.00")
        devolucao = self._devolucao(venda, [(item, 1, "50.00")])

        restaurado = restaurar_cashback_utilizado_devolucao(devolucao)
        devolucao.refresh_from_db()

        self.assertEqual(restaurado, Decimal("15.00"))
        self.assertEqual(devolucao.credito_cliente, Decimal("35.00"))

    def test_duas_parciais_acumuladas_restituem_incremental_e_total_integral_exato(self):
        venda, item = self._venda("100.00", quantidade=2)
        self._uso_cashback(venda, "30.00")
        primeira = self._devolucao(venda, [(item, 1, "50.00")], documento="DEV-CBK-P1")
        segunda = self._devolucao(venda, [(item, 1, "50.00")], documento="DEV-CBK-P2")

        self.assertEqual(restaurar_cashback_utilizado_devolucao(primeira), Decimal("15.00"))
        self.assertEqual(restaurar_cashback_utilizado_devolucao(segunda), Decimal("15.00"))

        total = CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_ESTORNO, devolucao__venda=venda).aggregate(total=Sum("valor"))["total"]
        segunda.refresh_from_db()
        self.assertEqual(total, Decimal("30.00"))
        self.assertEqual(segunda.credito_cliente, Decimal("35.00"))

    def test_venda_sem_cashback_usado_preserva_vale_integral(self):
        venda, item = self._venda("100.00")
        devolucao = self._devolucao(venda, [(item, 1, "100.00")])

        restaurado = restaurar_cashback_utilizado_devolucao(devolucao)
        devolucao.refresh_from_db()

        self.assertEqual(restaurado, Decimal("0.00"))
        self.assertEqual(devolucao.credito_cliente, Decimal("100.00"))
        self.assertFalse(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_ESTORNO).exists())

    def test_venda_integralmente_paga_em_cashback_nao_gera_vale_zero(self):
        venda, item = self._venda("100.00")
        self._uso_cashback(venda, "100.00")
        devolucao = self._devolucao(venda, [(item, 1, "100.00")])

        restaurar_cashback_utilizado_devolucao(devolucao)
        devolucao.refresh_from_db()
        VendaDevolucaoViewSet()._registrar_credito_cliente(devolucao)

        self.assertEqual(devolucao.credito_cliente, Decimal("0.00"))
        self.assertFalse(ValeTroca.objects.filter(devolucao=devolucao).exists())

    def test_retry_do_servico_nao_duplica_estorno(self):
        venda, item = self._venda("100.00")
        self._uso_cashback(venda, "30.00")
        devolucao = self._devolucao(venda, [(item, 1, "100.00")])

        self.assertEqual(restaurar_cashback_utilizado_devolucao(devolucao), Decimal("30.00"))
        self.assertEqual(restaurar_cashback_utilizado_devolucao(devolucao), Decimal("30.00"))

        self.assertEqual(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_ESTORNO, devolucao=devolucao).count(), 1)

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Cliente, Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from compras.models import PedidoCompra
from financeiro.models import Caixa, ContaBancaria, LancamentoContabil, MovimentacaoFinanceira, Pagar, Receber
from fiscal.models import VendaPdv
from produto.models import (
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    Grade,
    Grupo,
    Produto,
    ProdutoDetalhe,
    Tamanho,
    Tabelapreco,
    TabelaprecoProduto,
    Unidade,
)


class MultiEmpresaIsolationTests(TestCase):
    """Garante que um usuario de uma empresa nao lista dados de outra empresa."""

    def setUp(self):
        self.client = APIClient()
        self.empresa_a = Empresa.objects.create(nome="Empresa Isolamento A", documento="11111111000191")
        self.empresa_b = Empresa.objects.create(nome="Empresa Isolamento B", documento="22222222000102")
        self.user_a = self._user("admin_a", self.empresa_a)
        self.user_b = self._user("admin_b", self.empresa_b)
        self.ctx_a = self._contexto_empresa(self.empresa_a, "A", "11111111000191")
        self.ctx_b = self._contexto_empresa(self.empresa_b, "B", "22222222000102")

    def _user(self, username, empresa):
        user = get_user_model().objects.create_user(
            username=username,
            password="12345678",
            type="Admin",
            empresa=empresa,
            is_staff=True,
        )
        return user

    def _contexto_empresa(self, empresa, sufixo, cnpj):
        loja = Loja.objects.create(
            empresa=empresa,
            nome_loja=f"Loja Isolamento {sufixo}",
            apelido_loja=f"L{sufixo}",
            cnpj=cnpj,
            cidade="Sao Paulo",
            estado="SP",
            email=f"loja{sufixo.lower()}@teste.local",
        )
        cliente = Cliente.objects.create(
            empresa=empresa,
            nome_cliente=f"Cliente Isolamento {sufixo}",
            apelido=f"CLI{sufixo}",
            cpf=f"0000000000{sufixo == 'B' and 2 or 1}",
            cidade="Sao Paulo",
        )
        fornecedor = Fornecedor.objects.create(
            empresa=empresa,
            nome_fornecedor=f"Fornecedor Isolamento {sufixo}",
            apelido=f"FOR{sufixo}",
            cnpj=f"{'33' if sufixo == 'A' else '44'}3333330001{'91' if sufixo == 'A' else '02'}",
            cidade="Sao Paulo",
        )
        vendedor = Funcionarios.objects.create(
            empresa=empresa,
            nomefuncionario=f"Vendedor Isolamento {sufixo}",
            apelido=f"VEND{sufixo}",
            categoria="Vendedor",
            idloja=loja,
            ativo=True,
        )
        natureza = Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo=f"9.{sufixo == 'B' and 2 or 1}",
            categoria_principal="Teste",
            subcategoria="Isolamento",
            descricao=f"Natureza Isolamento {sufixo}",
            tipo="RECEITA",
            status="ATIVO",
            tipo_natureza="CREDITO",
            natureza_operacao="RECEITA",
            ativo=True,
        )
        conta_caixa = PlanoContabil.objects.create(
            empresa=empresa,
            codigo=f"1.1.1{sufixo == 'B' and 2 or 1}",
            descricao=f"Conta Caixa Isolamento {sufixo}",
            classe=PlanoContabil.CLASSE_ATIVO,
            natureza=PlanoContabil.NATUREZA_DEBITO,
            nivel=3,
            analitica=True,
            ativa=True,
        )
        conta_receita = PlanoContabil.objects.create(
            empresa=empresa,
            codigo=f"3.1.1{sufixo == 'B' and 2 or 1}",
            descricao=f"Conta Receita Isolamento {sufixo}",
            classe=PlanoContabil.CLASSE_RECEITA,
            natureza=PlanoContabil.NATUREZA_CREDITO,
            nivel=3,
            analitica=True,
            ativa=True,
        )
        caixa = Caixa.objects.create(
            empresa=empresa,
            idloja=loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo=f"CX{sufixo}",
            descricao=f"Caixa Isolamento {sufixo}",
            saldo_atual=Decimal("100.00"),
            ativo=True,
        )
        conta = ContaBancaria.objects.create(
            empresa=empresa,
            idloja=loja,
            descricao=f"Conta Isolamento {sufixo}",
            banco="Banco Teste",
            agencia=f"000{sufixo == 'B' and 2 or 1}",
            conta=f"1234{sufixo == 'B' and 2 or 1}-0",
            saldo_atual=Decimal("100.00"),
            ativo=True,
        )
        unidade = Unidade.objects.create(empresa=empresa, Descricao=f"Unidade {sufixo}", Codigo=f"UN{sufixo}")
        grade = Grade.objects.create(empresa=empresa, Descricao=f"Grade {sufixo}", Status="ATIVO")
        tamanho = Tamanho.objects.create(empresa=empresa, idgrade=grade, Tamanho="M", Descricao="Medio", Status="ATIVO")
        cor = Cor.objects.create(empresa=empresa, Descricao=f"Cor {sufixo}", Codigo=f"C{sufixo}", Cor=f"Cor {sufixo}", Status="ATIVO")
        colecao = Colecao.objects.create(empresa=empresa, Descricao=f"Colecao {sufixo}", Codigo="26", Estacao="01", Status="AT")
        grupo = Grupo.objects.create(empresa=empresa, Codigo=f"G{sufixo}", CodigoRef=f"0{sufixo == 'B' and 2 or 1}", Descricao=f"Grupo {sufixo}", Margem=Decimal("50.00"))
        ConfigEan.objects.create(empresa=empresa, country_prefix="789", company_prefix=f"90{sufixo == 'B' and 2 or 1}0", next_itemref=1, ativo=True)
        produto = Produto.objects.create(
            empresa=empresa,
            tipo_produto="1",
            descricao=f"Produto Isolamento {sufixo}",
            descricao_reduzida=f"PROD{sufixo}",
            unidade=unidade,
            grupo=grupo,
            colecao=colecao,
            grade=grade,
            ncm="6204.62.00",
        )
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        tabela = Tabelapreco.objects.create(empresa=empresa, NomeTabela=f"Tabela {sufixo}", DataInicio=timezone.localdate())
        TabelaprecoProduto.objects.create(produto=produto, tabela=tabela, preco=Decimal("99.90"))
        estoque = Estoque.objects.create(CodigodeBarra=sku.ean13, referencia=produto.referencia, Idloja=loja, Estoque=10, reserva=0)
        EstoqueMovimentacao.objects.create(
            Idloja=loja,
            CodigodeBarra=sku.ean13,
            referencia=produto.referencia,
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=10,
            saldo_anterior=0,
            saldo_posterior=10,
            documento=f"DOC-EST-{sufixo}",
        )
        pedido = PedidoCompra.objects.create(
            empresa=empresa,
            tipo="1",
            loja=loja,
            fornecedor=fornecedor,
            emissao=timezone.localdate(),
            previsao_entrega=timezone.localdate(),
            total_pedido=Decimal("500.00"),
            status="AB",
            observacoes=f"Pedido Isolamento {sufixo}",
        )
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            caixa=caixa,
            cliente=cliente,
            vendedor=vendedor,
            documento=f"VENDA-ISO-{sufixo}",
            forma_pagamento="DINHEIRO",
            total=Decimal("99.90"),
            valor_recebido=Decimal("99.90"),
        )
        pagar = Pagar.objects.create(
            empresa=empresa,
            idloja=loja,
            idfornecedor=fornecedor,
            Titulo=f"PAGAR-ISO-{sufixo}",
            Documento=f"NF-{sufixo}",
            Data_emissao=timezone.localdate(),
            Valor_total=Decimal("500.00"),
            Idnatureza=natureza,
        )
        receber = Receber.objects.create(
            empresa=empresa,
            idloja=loja,
            idcliente=cliente,
            Titulo=f"RECEBER-ISO-{sufixo}",
            Documento=f"REC-{sufixo}",
            Data_emissao=timezone.localdate(),
            Valor_total=Decimal("99.90"),
            Idnatureza=natureza,
        )
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=empresa,
            idloja=loja,
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
            valor=Decimal("99.90"),
            historico=f"Movimento Isolamento {sufixo}",
            documento=f"MOV-ISO-{sufixo}",
            Idnatureza=natureza,
            caixa=caixa,
        )
        lancamento = LancamentoContabil.objects.create(
            empresa=empresa,
            idloja=loja,
            movimentacao=movimento,
            data_lancamento=timezone.localdate(),
            documento=f"LC-ISO-{sufixo}",
            historico=f"Lancamento Contabil Isolamento {sufixo}",
            origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
            natureza=natureza,
            conta_debito=conta_caixa,
            conta_credito=conta_receita,
            valor=Decimal("99.90"),
            status=LancamentoContabil.STATUS_GERADO,
        )
        return {
            "loja": loja,
            "cliente": cliente,
            "fornecedor": fornecedor,
            "vendedor": vendedor,
            "produto": produto,
            "estoque": estoque,
            "pedido": pedido,
            "venda": venda,
            "pagar": pagar,
            "receber": receber,
            "caixa": caixa,
            "conta": conta,
            "movimento": movimento,
            "lancamento": lancamento,
        }

    def _payload_text(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return json.dumps(response.json(), default=str, ensure_ascii=False)

    def _assert_isolado(self, url, marcador_proprio, marcador_outra_empresa):
        texto = self._payload_text(url)
        self.assertIn(marcador_proprio, texto, url)
        self.assertNotIn(marcador_outra_empresa, texto, url)

    def test_admin_enxerga_apenas_dados_da_propria_empresa(self):
        self.client.force_authenticate(self.user_a)

        checks = [
            ("/api/cadastros/lojas/", "Loja Isolamento A", "Loja Isolamento B"),
            ("/api/cadastros/clientes/", "Cliente Isolamento A", "Cliente Isolamento B"),
            ("/api/cadastros/fornecedores/", "Fornecedor Isolamento A", "Fornecedor Isolamento B"),
            ("/api/cadastros/funcionarios/", "Vendedor Isolamento A", "Vendedor Isolamento B"),
            ("/api/produto/produto/", "Produto Isolamento A", "Produto Isolamento B"),
            ("/api/produto/estoque/", self.ctx_a["produto"].referencia, self.ctx_b["produto"].referencia),
            ("/api/produto/estoque-movimentacao/", "DOC-EST-A", "DOC-EST-B"),
            ("/api/compras/pedidos/", "Pedido Isolamento A", "Pedido Isolamento B"),
            ("/api/fiscal/vendas-pdv/", "VENDA-ISO-A", "VENDA-ISO-B"),
            ("/api/financeiro/pagar/", "PAGAR-ISO-A", "PAGAR-ISO-B"),
            ("/api/financeiro/receber/", "RECEBER-ISO-A", "RECEBER-ISO-B"),
            ("/api/financeiro/caixas/", "Caixa Isolamento A", "Caixa Isolamento B"),
            ("/api/financeiro/contas-bancarias/", "Conta Isolamento A", "Conta Isolamento B"),
            ("/api/financeiro/movimentacoes/", "MOV-ISO-A", "MOV-ISO-B"),
            ("/api/financeiro/lancamentos-contabeis/", "LC-ISO-A", "LC-ISO-B"),
        ]
        for url, proprio, outra_empresa in checks:
            with self.subTest(url=url):
                self._assert_isolado(url, proprio, outra_empresa)

# Create your tests here.

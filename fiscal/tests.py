from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Empresa, Fornecedor, Loja
from compras.models import PedidoCompra, PedidoCompraItem
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem
from produto.models import Produto, Unidade


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaMultiempresaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa_a = Empresa.objects.create(nome="Empresa A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B", documento="22222222000191", plano_completo=True)
        self.user_a = get_user_model().objects.create_superuser(
            "fiscal-a",
            "fiscal-a@sysvar.test",
            "test",
            type="Gerente",
            empresa=self.empresa_a,
        )
        self.client.force_authenticate(self.user_a)
        self.loja_a = Loja.objects.create(
            empresa=self.empresa_a,
            nome_loja="Loja A",
            apelido_loja="Loja A",
            cnpj="11111111000100",
            estado="SP",
        )
        self.loja_b = Loja.objects.create(
            empresa=self.empresa_b,
            nome_loja="Loja B",
            apelido_loja="Loja B",
            cnpj="22222222000100",
            estado="SP",
        )
        self.fornecedor_a = self.criar_fornecedor(self.empresa_a, "Fornecedor A", "12345678000195")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "22345678000195")
        self.unidade_a = Unidade.objects.create(empresa=self.empresa_a, Descricao="Unidade", Codigo="UN")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B", Codigo="UNB")
        self.produto_a = Produto.objects.create(
            empresa=self.empresa_a,
            tipo_produto="2",
            descricao="Uso A",
            unidade=self.unidade_a,
        )
        self.produto_b = Produto.objects.create(
            empresa=self.empresa_b,
            tipo_produto="2",
            descricao="Uso B",
            unidade=self.unidade_b,
        )
        self.pedido_a = self.criar_pedido(self.empresa_a, self.loja_a, self.fornecedor_a)
        self.pedido_b = self.criar_pedido(self.empresa_b, self.loja_b, self.fornecedor_b)
        self.item_a = self.criar_item(self.pedido_a, self.produto_a)
        self.item_b = self.criar_item(self.pedido_b, self.produto_b)
        self.nota_a = self.criar_nota(self.pedido_a, "100")
        self.nota_b = self.criar_nota(self.pedido_b, "200")
        self.nota_item_a = NotaFiscalEntradaItem.objects.create(
            nota=self.nota_a,
            pedido_item=self.item_a,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )
        self.nota_item_b = NotaFiscalEntradaItem.objects.create(
            nota=self.nota_b,
            pedido_item=self.item_b,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, empresa, loja, fornecedor):
        return PedidoCompra.objects.create(
            empresa=empresa,
            tipo="2",
            loja=loja,
            fornecedor=fornecedor,
            status="AP",
            observacoes="Pedido teste",
        )

    def criar_item(self, pedido, produto):
        return PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            qtd=Decimal("2.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("20.00"),
        )

    def criar_nota(self, pedido, numero):
        return NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=timezone.localdate(),
            dt_entrada=timezone.localdate(),
        )

    def payload_nota(self, pedido, numero="300"):
        hoje = timezone.localdate().isoformat()
        return {
            "pedido_compra": pedido.id,
            "modelo": "55",
            "serie": "1",
            "numero": numero,
            "dt_emissao": hoje,
            "dt_entrada": hoje,
        }

    def payload_item(self, nota, pedido_item):
        return {
            "nota": nota.id,
            "pedido_item": pedido_item.id,
            "qtd_recebida": "1.000",
            "preco_unit_nf": "10.0000",
            "desconto_item": "0.00",
        }

    def test_lista_e_filtros_respeitam_empresa_da_requisicao(self):
        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(self.nota_a.id, [n["id"] for n in notas])
        self.assertNotIn(self.nota_b.id, [n["id"] for n in notas])

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "pedido": self.pedido_b.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(notas, [])

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "numero": self.nota_b.numero})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(notas, [])

    def test_get_direto_de_nota_de_outra_empresa_retorna_404(self):
        resp = self.client.get(f"/api/fiscal/notas-entrada/{self.nota_b.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 404)

    def test_create_e_update_bloqueiam_pedido_de_outra_empresa(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota(self.pedido_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.patch(
            f"/api/fiscal/notas-entrada/{self.nota_a.id}/?empresa={self.empresa_a.id}",
            {"pedido_compra": self.pedido_b.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_itens_respeitam_empresa_e_filtros_nao_escapam_tenant(self):
        resp = self.client.get(f"/api/fiscal/notas-entrada-itens/{self.nota_item_b.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 404)

        resp = self.client.get("/api/fiscal/notas-entrada-itens/", {"empresa": self.empresa_a.id, "nota": self.nota_b.id})
        itens = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(itens, [])

        resp = self.client.get("/api/fiscal/notas-entrada-itens/", {"empresa": self.empresa_a.id, "pedido_item": self.item_b.id})
        itens = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(itens, [])

    def test_create_e_update_item_bloqueiam_relacionamentos_cruzados(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada-itens/?empresa={self.empresa_a.id}",
            self.payload_item(self.nota_b, self.item_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.post(
            f"/api/fiscal/notas-entrada-itens/?empresa={self.empresa_a.id}",
            self.payload_item(self.nota_a, self.item_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.patch(
            f"/api/fiscal/notas-entrada-itens/{self.nota_item_a.id}/?empresa={self.empresa_a.id}",
            {"pedido_item": self.item_b.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_delete_direto_de_nota_fiscal_entrada_e_bloqueado(self):
        resp = self.client.delete(f"/api/fiscal/notas-entrada/{self.nota_a.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 405, resp.data)
        self.assertTrue(NotaFiscalEntrada.objects.filter(pk=self.nota_a.pk).exists())

from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso
from cadastros.models import Cliente, Empresa, EmpresaContrato, Funcionarios, Loja, ModuloSistema
from financeiro.models import Caixa
from fiscal.models import NFCe, VendaPdv, VendaPdvItem, VendaPdvPagamento
from produto.models import ConfigEan, Cor, Grade, Produto, ProdutoDetalhe, Tamanho, Unidade


class VendaPdvConsultaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.modulo_vendas, _ = ModuloSistema.objects.get_or_create(
            chave="vendas",
            defaults={
                "nome": "Vendas",
                "categoria": ModuloSistema.CATEGORIA_COMERCIAL,
                "basico": False,
                "ativo": True,
            },
        )
        self.empresa = Empresa.objects.create(nome="Empresa Consulta", documento="11222333000181", plano_completo=True)
        self.outra_empresa = Empresa.objects.create(nome="Outra Consulta", documento="21222333000181", plano_completo=True)
        self.user = self._usuario_empresa(self.empresa, "consulta")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Consulta", apelido_loja="CONS", cnpj="11222333000181", estado="SP")
        self.outra_loja = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Outra Loja", apelido_loja="OCON", cnpj="21222333000181", estado="SP")
        self.caixa = Caixa.objects.create(empresa=self.empresa, idloja=self.loja, tipo_caixa=Caixa.TIPO_LOJA, codigo="CX1", descricao="Caixa Consulta")
        self.cliente = Cliente.objects.create(empresa=self.empresa, tipo_pessoa="PF", documento="39053344705", cpf="39053344705", nome_cliente="Maria Consulta")
        self.outro_cliente = Cliente.objects.create(empresa=self.outra_empresa, tipo_pessoa="PF", documento="52998224725", cpf="52998224725", nome_cliente="Cliente Outra")
        self.vendedor = Funcionarios.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            nomefuncionario="Vendedor Consulta",
            cpf="39053344705",
            matricula="000001",
            participa_vendas=True,
            ativo=True,
        )
        self.outro_vendedor = Funcionarios.objects.create(
            empresa=self.outra_empresa,
            idloja=self.outra_loja,
            nomefuncionario="Vendedor Outra",
            cpf="52998224725",
            matricula="000001",
            participa_vendas=True,
            ativo=True,
        )
        self.produto, self.sku = self._produto_sku(self.empresa)
        self.outro_produto, self.outro_sku = self._produto_sku(self.outra_empresa, prefixo="3344")

    def _usuario_empresa(self, empresa, username):
        perfil, _ = PerfilAcesso.objects.get_or_create(
            empresa=empresa,
            nome="Administrador delegado",
            defaults={"ativo": True},
        )
        user = get_user_model().objects.create_user(username=username, password="senha", empresa=empresa, type="Admin", perfil_principal=perfil)
        EmpresaContrato.objects.update_or_create(
            empresa=empresa,
            defaults={
                "status": EmpresaContrato.STATUS_ATIVO,
                "plano_completo": True,
                "limite_sessoes_simultaneas": 5,
            },
        )
        return user

    def _produto_sku(self, empresa, prefixo="2234"):
        unidade = Unidade.objects.create(empresa=empresa, Codigo=f"UN{prefixo[-1]}", Descricao="UNIDADE")
        grade = Grade.objects.create(empresa=empresa, Descricao=f"Grade {prefixo}")
        cor = Cor.objects.create(empresa=empresa, Descricao=f"AZUL {prefixo}", Codigo=prefixo[-2:], Cor="Azul")
        tamanho = Tamanho.objects.create(empresa=empresa, idgrade=grade, Tamanho=prefixo[-2:], Descricao=prefixo[-2:])
        ConfigEan.objects.create(empresa=empresa, company_prefix=prefixo)
        produto = Produto.objects.create(
            empresa=empresa,
            referencia=f"REF-{prefixo}",
            descricao=f"Produto {prefixo}",
            unidade=unidade,
            ncm="6204.62.00",
            origem_mercadoria=0,
            cfop_venda_dentro="5102",
        )
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        return produto, sku

    def _dt(self, value):
        data = datetime.fromisoformat(value)
        return data if timezone.is_aware(data) else timezone.make_aware(data)

    def _venda(self, documento, data_venda, empresa=None, loja=None, cliente=None, vendedor=None, produto=None, sku=None, total="100.00", pagamentos=None):
        empresa = empresa or self.empresa
        loja = loja or self.loja
        cliente = cliente or self.cliente
        vendedor = vendedor or self.vendedor
        produto = produto or self.produto
        sku = sku or self.sku
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            caixa=self.caixa if loja == self.loja else None,
            cliente=cliente,
            vendedor=vendedor,
            documento=documento,
            status=VendaPdv.Status.FINALIZADA,
            forma_pagamento="CRE",
            data_venda=self._dt(data_venda),
            subtotal=Decimal(total),
            desconto_itens=Decimal("0.00"),
            desconto_geral=Decimal("0.00"),
            total=Decimal(total),
            valor_recebido=Decimal(total),
        )
        VendaPdvItem.objects.create(
            venda=venda,
            produto=produto,
            sku=sku,
            ean=sku.ean13,
            referencia=produto.referencia,
            descricao=produto.descricao,
            cor="AZUL",
            tamanho="40",
            quantidade=1,
            preco_unitario=Decimal(total),
            desconto=Decimal("0.00"),
        )
        for pagamento in pagamentos or [{"forma": "CRE", "descricao": "Cartão", "valor": total}]:
            VendaPdvPagamento.objects.create(venda=venda, **pagamento)
        return venda

    def test_consulta_vendas_lista_com_escopo_periodo_filtros_paginacao_e_ordem(self):
        venda_antiga = self._venda("VE0010000001", "2026-10-08T09:00:00-03:00")
        venda_recente = self._venda("VE0010000002", "2026-10-09T10:00:00-03:00")
        self._venda("VE0010000003", "2026-10-01T10:00:00-03:00")
        self._venda(
            "VE9990000001",
            "2026-10-09T11:00:00-03:00",
            empresa=self.outra_empresa,
            loja=self.outra_loja,
            cliente=self.outro_cliente,
            vendedor=self.outro_vendedor,
            produto=self.outro_produto,
            sku=self.outro_sku,
        )

        response = self.client.get("/api/fiscal/vendas-pdv/consulta-vendas/", {
            "data_ini": "2026-10-08",
            "data_fim": "2026-10-09",
            "loja": self.loja.pk,
            "vendedor": self.vendedor.pk,
            "documento": "VE001",
            "page_size": 1,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["page"], 1)
        self.assertEqual(response.data["page_size"], 1)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertEqual([row["documento"] for row in response.data["results"]], [venda_recente.documento])

        response = self.client.get("/api/fiscal/vendas-pdv/consulta-vendas/", {
            "data_ini": "2026-10-08",
            "data_fim": "2026-10-09",
            "loja": self.loja.pk,
            "vendedor": self.vendedor.pk,
            "documento": "VE001",
            "page": 2,
            "page_size": 1,
        })

        self.assertEqual([row["documento"] for row in response.data["results"]], [venda_antiga.documento])

    def test_consulta_vendas_filtra_cliente_pagamento_nfce_sem_duplicar(self):
        venda = self._venda(
            "VE0010000010",
            "2026-10-09T10:00:00-03:00",
            pagamentos=[
                {"forma": "CRE", "descricao": "Cartão 1", "valor": "60.00"},
                {"forma": "CRE", "descricao": "Cartão 2", "valor": "40.00"},
            ],
        )
        NFCe.objects.create(venda=venda, loja=self.loja, serie=1, numero=321, status=NFCe.Status.AUTORIZADA, chave_acesso="123" * 14 + "12", protocolo="PROTO321")

        response = self.client.get("/api/fiscal/vendas-pdv/consulta-vendas/", {
            "cliente": "Maria",
            "forma_pagamento": "CRE",
            "nfce": "321",
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["documento"], venda.documento)
        self.assertEqual(response.data["results"][0]["nfce"]["numero"], 321)

    def test_consulta_venda_detalhe_retorna_dados_e_respeita_escopo(self):
        venda = self._venda("VE0010000020", "2026-10-09T10:00:00-03:00")
        nfce = NFCe.objects.create(venda=venda, loja=self.loja, serie=2, numero=456, status=NFCe.Status.AUTORIZADA, chave_acesso="456" * 14 + "45", protocolo="PROTO456")
        venda_outra = self._venda(
            "VE9990000020",
            "2026-10-09T11:00:00-03:00",
            empresa=self.outra_empresa,
            loja=self.outra_loja,
            cliente=self.outro_cliente,
            vendedor=self.outro_vendedor,
            produto=self.outro_produto,
            sku=self.outro_sku,
        )

        response = self.client.get(f"/api/fiscal/vendas-pdv/{venda.pk}/consulta/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["documento"], venda.documento)
        self.assertEqual(response.data["loja"]["id"], self.loja.pk)
        self.assertEqual(response.data["cliente"]["nome"], self.cliente.nome_cliente)
        self.assertEqual(response.data["vendedor"]["nome"], self.vendedor.nomefuncionario)
        self.assertEqual(len(response.data["itens"]), 1)
        self.assertEqual(response.data["itens"][0]["ean"], self.sku.ean13)
        self.assertEqual(len(response.data["pagamentos"]), 1)
        self.assertEqual(response.data["nfce"]["id"], nfce.pk)
        self.assertEqual(response.data["nfce"]["numero"], 456)
        self.assertNotIn("xml", response.data["nfce"])

        response = self.client.get(f"/api/fiscal/vendas-pdv/{venda_outra.pk}/consulta/")

        self.assertEqual(response.status_code, 404)

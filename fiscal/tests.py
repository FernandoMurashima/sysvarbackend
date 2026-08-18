from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Fornecedor, Loja, ModuloSistema, Nat_Lancamento
from compras.models import PedidoCompra, PedidoCompraItem
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem
from financeiro.models import MovimentacaoFinanceira, Pagar, PagarItem
from produto.models import Colecao, ConfigEan, Cor, Estoque, EstoqueMovimentacao, Grade, Grupo, Pack, PackItem, Produto, ProdutoDetalhe, Tamanho, Unidade


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


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaPermissoesBloco4Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.compras_modulo = ModuloSistema.objects.update_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        self.fiscal_modulo = ModuloSistema.objects.update_or_create(
            chave="fiscal",
            defaults={"nome": "Fiscal", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        self.empresa = self.criar_empresa("Empresa Permissoes", "44444444000191")
        self.outra_empresa = self.criar_empresa("Outra Empresa Permissoes", "55555555000191")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Permissoes",
            apelido_loja="LP",
            cnpj="44444444000100",
            estado="SP",
        )
        self.outra_loja = Loja.objects.create(
            empresa=self.outra_empresa,
            nome_loja="Loja Outra Permissoes",
            apelido_loja="LOP",
            cnpj="55555555000100",
            estado="SP",
        )
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor Permissoes", "44445678000195")
        self.outra_fornecedor = self.criar_fornecedor(self.outra_empresa, "Fornecedor Outra Permissoes", "55545678000195")
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        self.outra_unidade = Unidade.objects.create(empresa=self.outra_empresa, Descricao="Unidade Outra", Codigo="UO")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Uso Permissoes", unidade=self.unidade)
        self.outra_produto = Produto.objects.create(
            empresa=self.outra_empresa,
            tipo_produto="2",
            descricao="Uso Outra Permissoes",
            unidade=self.outra_unidade,
        )
        self.pedido = self.criar_pedido(self.empresa, self.loja, self.fornecedor)
        self.outra_pedido = self.criar_pedido(self.outra_empresa, self.outra_loja, self.outra_fornecedor)
        self.pedido_item = self.criar_item(self.pedido, self.produto)
        self.outra_pedido_item = self.criar_item(self.outra_pedido, self.outra_produto)
        self.nota = self.criar_nota(self.pedido, "9001")
        self.outra_nota = self.criar_nota(self.outra_pedido, "9002")
        self.nota_item = NotaFiscalEntradaItem.objects.create(
            nota=self.nota,
            pedido_item=self.pedido_item,
            qtd_recebida=Decimal("2.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("20.00"),
        )
        self.outra_nota_item = NotaFiscalEntradaItem.objects.create(
            nota=self.outra_nota,
            pedido_item=self.outra_pedido_item,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )
        self.user_compras_edit = self.criar_usuario("compras-edit", self.empresa, compras=UserModulePermission.Access.EDIT)
        self.user_compras_view = self.criar_usuario("compras-view", self.empresa, compras=UserModulePermission.Access.VIEW)
        self.user_sem_compras = self.criar_usuario("sem-compras", self.empresa)
        self.user_fiscal_edit = self.criar_usuario("fiscal-edit", self.empresa, fiscal=UserModulePermission.Access.EDIT)
        self.user_outra_empresa = self.criar_usuario("compras-outra", self.outra_empresa, compras=UserModulePermission.Access.EDIT)

    def criar_empresa(self, nome, documento):
        empresa = Empresa.objects.create(nome=nome, documento=documento, plano_completo=False, usa_compras=True, usa_fiscal=False)
        EmpresaContrato.objects.update_or_create(
            empresa=empresa,
            defaults={
                "status": EmpresaContrato.STATUS_ATIVO,
                "plano_completo": False,
                "limite_sessoes_simultaneas": 3,
            },
        )
        EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=self.compras_modulo, defaults={"contratado": True})
        EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=self.fiscal_modulo, defaults={"contratado": True})
        return empresa

    def criar_usuario(self, username, empresa, compras=None, fiscal=None):
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome=f"Perfil {username}")
        if compras:
            PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.compras_modulo, acesso=compras)
        if fiscal:
            PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.fiscal_modulo, acesso=fiscal)
        return get_user_model().objects.create_user(
            username=username,
            password="12345678",
            type="Gerente",
            empresa=empresa,
            perfil_principal=perfil,
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
            total_pedido=Decimal("20.00"),
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

    def test_usuario_com_compras_sem_fiscal_lista_abre_itens_e_endpoints_operacionais(self):
        self.client.force_authenticate(self.user_compras_edit)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        notas = lista.data.get("results", lista.data) if isinstance(lista.data, dict) else lista.data
        self.assertEqual(lista.status_code, 200)
        self.assertIn(self.nota.id, [nota["id"] for nota in notas])

        detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
        self.assertEqual(detalhe.status_code, 200)
        self.assertEqual(detalhe.data["id"], self.nota.id)

        itens = self.client.get("/api/fiscal/notas-entrada-itens/", {"nota": self.nota.id})
        itens_data = itens.data.get("results", itens.data) if isinstance(itens.data, dict) else itens.data
        self.assertEqual(itens.status_code, 200)
        self.assertEqual([item["id"] for item in itens_data], [self.nota_item.id])

        itens_pedido = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/itens-pedido/")
        self.assertEqual(itens_pedido.status_code, 200)
        self.assertEqual(itens_pedido.data[0]["pedido_item"], self.pedido_item.id)

        fechar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/fechar/", {}, format="json")
        self.assertEqual(fechar.status_code, 200, fechar.data)

        cancelar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/cancelar/", {}, format="json")
        self.assertEqual(cancelar.status_code, 200, cancelar.data)

    def test_usuario_sem_compras_e_usuario_apenas_fiscal_sao_bloqueados(self):
        for user in (self.user_sem_compras, self.user_fiscal_edit):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user)
                lista = self.client.get("/api/fiscal/notas-entrada/")
                detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
                itens = self.client.get("/api/fiscal/notas-entrada-itens/", {"nota": self.nota.id})
                fechar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/fechar/", {}, format="json")
                self.assertEqual(lista.status_code, 403)
                self.assertEqual(detalhe.status_code, 403)
                self.assertEqual(itens.status_code, 403)
                self.assertEqual(fechar.status_code, 403)

    def test_usuario_de_outra_empresa_com_compras_nao_acessa_registros_alheios(self):
        self.client.force_authenticate(self.user_outra_empresa)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        notas = lista.data.get("results", lista.data) if isinstance(lista.data, dict) else lista.data
        self.assertEqual(lista.status_code, 200)
        self.assertNotIn(self.nota.id, [nota["id"] for nota in notas])
        self.assertIn(self.outra_nota.id, [nota["id"] for nota in notas])

        detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
        item = self.client.get(f"/api/fiscal/notas-entrada-itens/{self.nota_item.id}/")
        self.assertEqual(detalhe.status_code, 404)
        self.assertEqual(item.status_code, 404)

    def test_nivel_view_em_compras_mantem_leitura_e_bloqueia_escrita(self):
        self.client.force_authenticate(self.user_compras_view)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        criar = self.client.post(
            "/api/fiscal/notas-entrada/",
            {
                "pedido_compra": self.pedido.id,
                "modelo": "55",
                "serie": "2",
                "numero": "9003",
                "dt_emissao": timezone.localdate().isoformat(),
                "dt_entrada": timezone.localdate().isoformat(),
            },
            format="json",
        )

        self.assertEqual(lista.status_code, 200)
        self.assertEqual(criar.status_code, 403)


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaCancelamentoBloco2Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Bloco 2", documento="33333333000191", plano_completo=True)
        self.user = get_user_model().objects.create_superuser("nf-cancel", "nf-cancel@sysvar.test", "test")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja B2",
            apelido_loja="Loja B2",
            cnpj="33333333000100",
            estado="SP",
            EstoqueNegativo="NAO",
        )
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="33345678000195",
            cnpj="33345678000195",
            nome_fornecedor="Fornecedor B2",
            categoria="USO_CONSUMO",
        )
        self.natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="CMPB2",
            categoria_principal="Compras",
            subcategoria="NF",
            descricao="Compra NF",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")

    def criar_produto(self, tipo="2", descricao="Produto"):
        return Produto.objects.create(empresa=self.empresa, tipo_produto=tipo, descricao=descricao, unidade=self.unidade)

    def criar_pedido(self, produto, qtd=Decimal("10.000"), preco=Decimal("10.00"), tipo="2"):
        pedido = PedidoCompra.objects.create(
            empresa=self.empresa,
            tipo=tipo,
            loja=self.loja,
            fornecedor=self.fornecedor,
            status="AP",
            total_pedido=qtd * preco,
        )
        item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            qtd=qtd,
            preco_unit=preco,
            total_item=qtd * preco,
        )
        self.criar_previsao(pedido)
        return pedido, item

    def criar_previsao(self, pedido, valor=None):
        valor = Decimal(valor or pedido.total_pedido).quantize(Decimal("0.01"))
        titulo = Pagar.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            idfornecedor=self.fornecedor,
            Titulo=f"PC {pedido.pk}",
            Data_emissao=timezone.localdate(),
            Valor_total=valor,
            Previsao=True,
            FormaPagamento="BOL",
            Idnatureza=self.natureza,
            pedido_compra=pedido.pk,
        )
        PagarItem.objects.create(
            Idpagar=titulo,
            parcela_n=1,
            status=PagarItem.STATUS_PREVISTO,
            Data_vencimento=timezone.localdate(),
            valor_parcela=valor,
            FormaPagamento="BOL",
            Previsao=True,
            Idnatureza=self.natureza,
        )
        return titulo

    def criar_nota(self, pedido, item, numero, qtd, preco):
        nota = NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=timezone.localdate(),
            dt_entrada=timezone.localdate(),
        )
        NotaFiscalEntradaItem.objects.create(
            nota=nota,
            pedido_item=item,
            qtd_recebida=qtd,
            preco_unit_nf=preco,
            total_item=(qtd * preco).quantize(Decimal("0.01")),
        )
        return nota

    def fechar(self, nota):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/fechar/?empresa={self.empresa.pk}", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def cancelar(self, nota, status_code=200):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={self.empresa.pk}", {}, format="json")
        self.assertEqual(resp.status_code, status_code, resp.data)
        nota.refresh_from_db()
        return resp

    def financeiro(self, pedido):
        return list(Pagar.objects.filter(pedido_compra=pedido.pk).order_by("Idpagar"))

    def test_cancelamento_nf_total_restaura_previsao(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "900", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.assertTrue(Pagar.objects.get(nfe_id=nota.pk).itens.filter(status=PagarItem.STATUS_EFETIVO).exists())

        self.cancelar(nota)
        self.assertFalse(Pagar.objects.filter(nfe_id=nota.pk).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("100.00"))
        self.assertEqual(previsao.itens.get().status, PagarItem.STATUS_PREVISTO)

    def test_cancelamento_parcial_preserva_financeiro_de_outra_nf(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "901", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "902", Decimal("6.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)

        self.cancelar(nota1)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota2.pk, Previsao=False).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("40.00"))

    def test_cancelamento_da_segunda_nf_preserva_primeira(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "903", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "904", Decimal("6.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)

        self.cancelar(nota2)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota1.pk, Previsao=False).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("60.00"))

    def test_cancelamento_bloqueia_parcela_baixada_e_faz_rollback(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "905", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        PagarItem.objects.filter(Idpagar__nfe_id=nota.pk).update(status=PagarItem.STATUS_BAIXADO, valor_baixa=Decimal("100.00"), data_baixa=timezone.localdate())

        self.cancelar(nota, status_code=400)
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_cancelamento_bloqueia_movimentacao_financeira_vinculada(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "917", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        parcela = PagarItem.objects.get(Idpagar__nfe_id=nota.pk)
        MovimentacaoFinanceira.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_PAGAR,
            valor=Decimal("100.00"),
            historico="Baixa teste",
            documento="NF917",
            pagar_item=parcela,
        )

        self.cancelar(nota, status_code=400)
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_cancelamento_repetido_nao_duplica_estornos_ou_previsoes(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "906", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.cancelar(nota)
        self.cancelar(nota)
        self.assertEqual(Pagar.objects.filter(pedido_compra=pedido.pk, Previsao=True).count(), 1)
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL").count(), 1)

    def test_uso_consumo_estorna_estoque_e_recalcula_custos(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto, qtd=Decimal("5.000"), preco=Decimal("20.00"))
        nota = self.criar_nota(pedido, item, "907", Decimal("5.000"), Decimal("20.00"))
        self.fechar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque, Decimal("5.000"))
        self.assertEqual(produto.__class__.objects.get(pk=produto.pk).custo_medio, Decimal("20.0000"))

        self.cancelar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque, Decimal("0.000"))
        self.assertEqual(produto.__class__.objects.get(pk=produto.pk).custo_medio, Decimal("0.0000"))

    def criar_revenda(self):
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="P", Descricao="P")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=0)
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao", Codigo="26", Estacao="01", Status="AT")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao="Revenda",
            unidade=self.unidade,
            grade=grade,
            grupo=grupo,
            colecao=colecao,
        )
        ConfigEan.objects.create(empresa=self.empresa, country_prefix="789", company_prefix="3333", ativo=True)
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        pack = Pack.objects.create(empresa=self.empresa, nome="Pack 1", grade=grade)
        PackItem.objects.create(pack=pack, tamanho=tamanho, qtd=1)
        pedido = PedidoCompra.objects.create(
            empresa=self.empresa,
            tipo="1",
            loja=self.loja,
            fornecedor=self.fornecedor,
            status="AP",
            total_pedido=Decimal("100.00"),
        )
        item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            cor=cor,
            pack=pack,
            n_packs=10,
            qtd=Decimal("10.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("100.00"),
        )
        self.criar_previsao(pedido)
        return pedido, item, sku

    def test_revenda_estorna_com_saldo_suficiente(self):
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "908", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.cancelar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("0.000"))

    def test_revenda_bloqueia_saldo_negativo_e_rollback_total(self):
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "909", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        Estoque.objects.filter(Idloja=self.loja, CodigodeBarra=sku.ean13).update(Estoque=Decimal("2.000"))

        self.cancelar(nota, status_code=400)
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("2.000"))
        self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_revenda_permite_saldo_negativo_quando_loja_permite(self):
        self.loja.EstoqueNegativo = "SIM"
        self.loja.save(update_fields=["EstoqueNegativo"])
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "910", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        Estoque.objects.filter(Idloja=self.loja, CodigodeBarra=sku.ean13).update(Estoque=Decimal("2.000"))

        self.cancelar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("-8.000"))

    def test_duas_entradas_recalculam_custos_apos_cancelar_primeira_ou_segunda(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto, preco=Decimal("16.00"))
        nota1 = self.criar_nota(pedido, item, "911", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "912", Decimal("6.000"), Decimal("20.00"))
        self.fechar(nota1)
        self.fechar(nota2)
        self.cancelar(nota1)
        produto.refresh_from_db()
        self.assertEqual(produto.custo_ultima_compra, Decimal("20.0000"))
        self.assertEqual(produto.custo_medio, Decimal("20.0000"))

        produto2 = self.criar_produto(descricao="Produto 2")
        pedido2, item2 = self.criar_pedido(produto2, preco=Decimal("16.00"))
        nota3 = self.criar_nota(pedido2, item2, "913", Decimal("4.000"), Decimal("10.00"))
        nota4 = self.criar_nota(pedido2, item2, "914", Decimal("6.000"), Decimal("20.00"))
        self.fechar(nota3)
        self.fechar(nota4)
        self.cancelar(nota4)
        produto2.refresh_from_db()
        self.assertEqual(produto2.custo_ultima_compra, Decimal("10.0000"))
        self.assertEqual(produto2.custo_medio, Decimal("10.0000"))

    def test_pedido_at_retorna_para_ap_quando_cancelamento_reduz_recebimento(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "915", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")
        self.cancelar(nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")

    def test_pedido_permanece_at_quando_outra_nf_valida_atende_totalmente(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "918", Decimal("10.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "919", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")

        self.cancelar(nota1)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")

    def test_impedimento_financeiro_nao_altera_estoque_custos_nf_ou_pedido(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "916", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        estoque_antes = Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque
        PagarItem.objects.filter(Idpagar__nfe_id=nota.pk).update(status=PagarItem.STATUS_BAIXADO, valor_baixa=Decimal("100.00"), data_baixa=timezone.localdate())

        self.cancelar(nota, status_code=400)
        self.assertEqual(NotaFiscalEntrada.objects.get(pk=nota.pk).status, NotaFiscalEntrada.Status.FECHADA)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque, estoque_antes)
        produto.refresh_from_db()
        self.assertEqual(produto.custo_medio, Decimal("10.0000"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaIdentidadeBloco3Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa B3", documento="44444444000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B3 Outra", documento="55555555000191", plano_completo=True)
        self.user = get_user_model().objects.create_superuser("nf-ident", "nf-ident@sysvar.test", "test")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B3", apelido_loja="Loja B3", cnpj="44444444000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B3B", apelido_loja="Loja B3B", cnpj="55555555000100", estado="SP")
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor 1", "44445678000195")
        self.fornecedor_2 = self.criar_fornecedor(self.empresa, "Fornecedor 2", "44445678000276")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "55545678000195")
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade B3", Codigo="U3")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B3B", Codigo="U4")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Produto B3", unidade=self.unidade)
        self.produto_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Produto B3B", unidade=self.unidade_b)

    def chave_valida(self, sequencia=1):
        base = f"35{timezone.localdate():%y%m}4444444400019155001000000{sequencia:03d}12345678"
        base = base[:43].ljust(43, "0")
        pesos = [2, 3, 4, 5, 6, 7, 8, 9]
        soma = sum(int(digito) * pesos[index % len(pesos)] for index, digito in enumerate(reversed(base)))
        dv = 11 - (soma % 11)
        if dv >= 10:
            dv = 0
        return f"{base}{dv}"

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, fornecedor=None, empresa=None, loja=None, produto=None, total=Decimal("10.00")):
        empresa = empresa or self.empresa
        loja = loja or self.loja
        fornecedor = fornecedor or self.fornecedor
        produto = produto or self.produto
        pedido = PedidoCompra.objects.create(empresa=empresa, tipo="2", loja=loja, fornecedor=fornecedor, status="AP", total_pedido=total)
        item = PedidoCompraItem.objects.create(pedido=pedido, produto=produto, qtd=Decimal("1.000"), preco_unit=total, total_item=total)
        return pedido, item

    def payload_nota(self, pedido, numero="123", serie="1", chave=None):
        payload = {
            "pedido_compra": pedido.pk,
            "modelo": "55",
            "serie": serie,
            "numero": numero,
            "dt_emissao": timezone.localdate().isoformat(),
            "dt_entrada": timezone.localdate().isoformat(),
        }
        if chave is not None:
            payload["chave_acesso"] = chave
        return payload

    def criar_nota_api(self, pedido, numero="123", serie="1", chave=None, status_code=201):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={pedido.empresa_id}",
            self.payload_nota(pedido, numero=numero, serie=serie, chave=chave),
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        return NotaFiscalEntrada.objects.get(pk=resp.data["id"]) if status_code == 201 else resp

    def criar_item(self, nota, item):
        NotaFiscalEntradaItem.objects.create(
            nota=nota,
            pedido_item=item,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=item.preco_unit,
            total_item=item.total_item,
        )
        nota.recalcular_totais()

    def fechar(self, nota):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/fechar/?empresa={nota.pedido_compra.empresa_id}", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def cancelar(self, nota):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={nota.pedido_compra.empresa_id}", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def test_identidade_documental_bloqueia_mesmo_fornecedor_empresa_modelo_serie_numero(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        self.criar_nota_api(pedido1, numero="100", serie="1")
        self.criar_nota_api(pedido2, numero="100", serie="1", status_code=400)

    def test_mesmo_numero_fornecedor_diferente_serie_diferente_empresa_diferente_e_pedido_igual_permitidos(self):
        pedido1, _ = self.criar_pedido()
        pedido_fornecedor_2, _ = self.criar_pedido(fornecedor=self.fornecedor_2)
        pedido_empresa_b, _ = self.criar_pedido(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, produto=self.produto_b)
        self.criar_nota_api(pedido1, numero="101", serie="1")
        self.criar_nota_api(pedido_fornecedor_2, numero="101", serie="1")
        self.criar_nota_api(pedido1, numero="101", serie="2")
        self.criar_nota_api(pedido_empresa_b, numero="101", serie="1")
        self.criar_nota_api(pedido1, numero="102", serie="1")

    def test_edicao_propria_permitida_e_colisao_bloqueada(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        nota1 = self.criar_nota_api(pedido1, numero="103")
        self.criar_nota_api(pedido2, numero="104")
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota1.pk}/?empresa={self.empresa.pk}", {"observacoes": "ok"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota1.pk}/?empresa={self.empresa.pk}", {"numero": "104"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_validacao_e_duplicidade_da_chave_de_acesso(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        chave = self.chave_valida(1)
        nota = self.criar_nota_api(pedido1, numero="105", chave=chave)
        self.assertEqual(nota.chave_acesso, chave)

        for chave_invalida in ("123", "1" * 45, "1" * 43 + "A", chave[:-1] + str((int(chave[-1]) + 1) % 10)):
            self.criar_nota_api(pedido2, numero=f"20{len(chave_invalida)}", chave=chave_invalida, status_code=400)

        self.criar_nota_api(pedido2, numero="106", chave=chave, status_code=400)
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota.pk}/?empresa={self.empresa.pk}", {"chave_acesso": chave}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        NotaFiscalEntrada.objects.filter(pk=nota.pk).update(status=NotaFiscalEntrada.Status.CANCELADA)
        self.criar_nota_api(pedido2, numero="107", chave=chave, status_code=400)

    def test_estoque_identifica_movimentos_por_id_da_nf_e_nao_por_numero(self):
        pedido1, item1 = self.criar_pedido()
        pedido2, item2 = self.criar_pedido(fornecedor=self.fornecedor_2)
        nota1 = self.criar_nota_api(pedido1, numero="108", serie="1")
        nota2 = self.criar_nota_api(pedido2, numero="108", serie="1")
        self.criar_item(nota1, item1)
        self.criar_item(nota2, item2)
        self.fechar(nota1)
        self.fechar(nota2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota1.pk}/fechar/?empresa={self.empresa.pk}", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota1.pk}:ENTRADA").count(), 1)
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota2.pk}:ENTRADA").count(), 1)

        self.cancelar(nota1)
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota1.pk}:CANCEL").count(), 1)
        self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota2.pk}:CANCEL").exists())

    def test_series_e_empresas_iguais_no_numero_movimentam_sem_interferencia(self):
        pedido1, item1 = self.criar_pedido()
        pedido2, item2 = self.criar_pedido()
        pedido_b, item_b = self.criar_pedido(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, produto=self.produto_b)
        nota1 = self.criar_nota_api(pedido1, numero="109", serie="1")
        nota2 = self.criar_nota_api(pedido2, numero="109", serie="2")
        nota_b = self.criar_nota_api(pedido_b, numero="109", serie="1")
        for nota, item in ((nota1, item1), (nota2, item2), (nota_b, item_b)):
            self.criar_item(nota, item)
            self.fechar(nota)
            self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA").count(), 1)

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserModulePermission
from cadastros.models import Empresa, EmpresaContrato, Fornecedor, Loja, ModuloSistema, Nat_Lancamento
from compras.models import PedidoCompra, PedidoCompraItem, PedidoCompraParcela, Requisicao, RequisicaoHistorico, RequisicaoItem, RequisicaoServicoCategoria, RequisicaoSetor
from financeiro.models import FormaPagamento, FormaPagamentoParcela, Pagar, PagarItem, PrazoPagamento, PrazoPagamentoParcela
from produto.models import Colecao, Cor, Grade, Grupo, Pack, PackItem, Produto, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao, Tamanho, Unidade
from auditoria.models import AuditLog


@override_settings(ALLOWED_HOSTS=["testserver"])
class PedidoCompraUnificadoTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_superuser("compras-admin", "compras-admin@sysvar.test", "test")
        self.client.force_authenticate(self.user)

        self.empresa = Empresa.objects.create(nome="Empresa A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B", documento="22222222000191", plano_completo=True)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja A", apelido_loja="Loja A", cnpj="11111111000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B", apelido_loja="Loja B", cnpj="22222222000100", estado="SP")
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor A", "12345678000195")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "22345678000195")
        self.un_int = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN", permite_decimal=False)
        self.un_dec = Unidade.objects.create(empresa=self.empresa, Descricao="Quilo", Codigo="KG", permite_decimal=True)
        self.grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        self.tam_p = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="P", Descricao="P")
        self.tam_m = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="M", Descricao="M")
        self.cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        self.grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=0)
        self.colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao", Codigo="26", Estacao="01", Status="AT")
        self.prod_revenda = self.criar_produto("1", "Revenda", self.un_int)
        self.prod_uso = self.criar_produto("2", "Uso", self.un_int)
        self.prod_uso_dec = self.criar_produto("2", "Uso Decimal", self.un_dec)
        self.prod_proprio = self.criar_produto("3", "Proprio", self.un_int)
        self.prod_insumo = self.criar_produto("4", "Insumo", self.un_dec)
        self.pack = Pack.objects.create(empresa=self.empresa, nome="Pack 3", grade=self.grade)
        PackItem.objects.create(pack=self.pack, tamanho=self.tam_p, qtd=1)
        PackItem.objects.create(pack=self.pack, tamanho=self.tam_m, qtd=2)
        self.natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="CMP",
            categoria_principal="Compras",
            subcategoria="Pedido",
            descricao="Compra de mercadorias",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        self.forma, self.prazo = self.criar_forma_pagamento()

    def criar_fornecedor(self, empresa, nome, documento, **extras):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="REVENDA",
            **extras,
        )

    def criar_produto(self, tipo, descricao, unidade, empresa=None):
        empresa = empresa or self.empresa
        kwargs = {"empresa": empresa, "tipo_produto": tipo, "descricao": descricao, "unidade": unidade}
        if tipo in ("1", "3"):
            kwargs.update({"grupo": self.grupo, "colecao": self.colecao, "grade": self.grade})
        return Produto.objects.create(**kwargs)

    def criar_forma_pagamento(self):
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30/60", descricao="30/60", num_parcelas=2)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("50.000000"))
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=2, dias=60, percentual=Decimal("50.000000"))
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            codigo="BOL",
            descricao="Boleto",
            tipo=FormaPagamento.TIPO_BOLETO,
            num_parcelas=2,
            prazo_pagamento=prazo,
        )
        FormaPagamentoParcela.objects.create(forma=forma, ordem=1, dias=30, percentual=Decimal("50.000000"))
        FormaPagamentoParcela.objects.create(forma=forma, ordem=2, dias=60, percentual=Decimal("50.000000"))
        return forma, prazo

    def criar_pedido(self, **extras):
        data = {"loja": self.loja.id, "fornecedor": self.fornecedor.id, "observacoes": "Pedido teste"}
        data.update(extras)
        resp = self.client.post("/api/compras/pedidos/", data, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return PedidoCompra.objects.get(pk=resp.data["id"])

    def payload_revenda(self, pedido, **extras):
        data = {
            "pedido": pedido.id,
            "produto": self.prod_revenda.Idproduto,
            "cor": self.cor.Idcor,
            "pack": self.pack.id,
            "n_packs": 2,
            "qtd": "999.000",
            "preco_unit": "10.00",
            "desconto_valor": "1.00",
        }
        data.update(extras)
        return data

    def payload_uso(self, pedido, produto=None, **extras):
        data = {
            "pedido": pedido.id,
            "produto": (produto or self.prod_uso).Idproduto,
            "qtd": "2.000",
            "preco_unit": "15.00",
            "desconto_valor": "0.00",
        }
        data.update(extras)
        return data

    def incluir_item(self, payload, status=201):
        resp = self.client.post("/api/compras/itens/", payload, format="json")
        self.assertEqual(resp.status_code, status, resp.data)
        if status == 201:
            return PedidoCompraItem.objects.get(pk=resp.data["id"])
        return resp

    def configurar_forma(self, pedido):
        resp = self.client.post(
            f"/api/compras/pedidos/{pedido.id}/set-forma-pagamento/",
            {"id_forma": self.forma.Idformapagamento, "id_prazo": self.prazo.Idprazo},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)


@override_settings(ALLOWED_HOSTS=["testserver"])
class RequisicaoCompraTests(PedidoCompraUnificadoTests):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.mod_compras, _ = ModuloSistema.objects.get_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "ativo": True, "ordem": 10},
        )
        EmpresaContrato.objects.update_or_create(empresa=self.empresa, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        EmpresaContrato.objects.update_or_create(empresa=self.empresa_b, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        self.solicitante = User.objects.create_user("req-assistente", "reqa@test.local", "123", empresa=self.empresa, loja=self.loja, type="AssistentePagar")
        self.aprovador = User.objects.create_user("req-gerente", "reqg@test.local", "123", empresa=self.empresa, loja=self.loja, type="Gerente")
        self.outro = User.objects.create_user("req-outro", "reqo@test.local", "123", empresa=self.empresa_b, loja=self.loja_b, type="Gerente")
        for user in (self.solicitante, self.aprovador, self.outro):
            UserModulePermission.objects.create(user=user, modulo="compras", acesso=UserModulePermission.Access.EDIT)
        self.unidade = self.un_int
        self.produto = self.prod_uso
        self.categoria = RequisicaoServicoCategoria.objects.create(empresa=self.empresa, nome="Informática")
        self.setor = RequisicaoSetor.objects.create(empresa=self.empresa, nome="Financeiro")
        self.setor_b = RequisicaoSetor.objects.create(empresa=self.empresa_b, nome="Financeiro B")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=self.produto, loja=self.loja, saldo=Decimal("10.000"))
        self.client.force_authenticate(self.solicitante)

    def criar_requisicao(self, **extras):
        data = {
            "loja": self.loja.id,
            "setor": self.setor.id,
            "data_necessaria": timezone.localdate().isoformat(),
            "prioridade": "NORMAL",
            "justificativa": "Reposição operacional",
            "observacoes": "Teste",
        }
        data.update(extras)
        resp = self.client.post("/api/compras/requisicoes/", data, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return Requisicao.objects.get(pk=resp.data["id"])

    def item_produto(self, req, qtd="5.000"):
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.produto.Idproduto,
            "unidade": self.unidade.Idunidade,
            "finalidade": "USO_CONSUMO",
            "qtd_solicitada": qtd,
            "observacoes": "Papel",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def aprovar(self, req):
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_usuario_sem_permissao_nao_aprova(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_usuario_nao_acessa_empresa_de_outra_requisicao(self):
        req_b = Requisicao.objects.create(numero=1, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, requisitante=self.outro, criado_por=self.outro, justificativa="B")
        resp = self.client.get("/api/compras/requisicoes/")
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(req_b.id, [r["id"] for r in rows])
        resp = self.client.get(f"/api/compras/requisicoes/{req_b.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_loja_deve_pertencer_a_empresa_correta(self):
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": self.loja_b.id,
            "setor": self.setor.id,
            "prioridade": "NORMAL",
            "justificativa": "Teste",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_setor_de_outra_empresa_nao_pode_ser_usado(self):
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": self.loja.id,
            "setor": self.setor_b.id,
            "prioridade": "NORMAL",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_campos_opcionais_e_prioridade_padrao(self):
        data = {
            "loja": self.loja.id,
            "setor": self.setor.id,
            "data_necessaria": None,
            "justificativa": "",
            "observacoes": "",
        }
        resp = self.client.post("/api/compras/requisicoes/", data, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        req = Requisicao.objects.get(pk=resp.data["id"])
        self.assertIsNone(req.data_necessaria)
        self.assertEqual(req.justificativa, "")
        self.assertEqual(req.observacoes, "")
        self.assertEqual(req.prioridade, "NORMAL")

    def test_empresa_nova_recebe_categorias_padrao_ao_listar(self):
        self.assertFalse(RequisicaoServicoCategoria.objects.filter(empresa=self.empresa_b).exists())
        self.client.force_authenticate(self.outro)
        resp = self.client.get("/api/compras/requisicao-servico-categorias/")
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertTrue(any(row["nome"] == "Informatica" for row in rows))
        self.assertTrue(RequisicaoServicoCategoria.objects.filter(empresa=self.empresa_b, nome="Informatica").exists())
        self.client.force_authenticate(self.solicitante)

    def test_setor_pode_ser_criado_editado_inativado_e_reativado(self):
        resp = self.client.post("/api/compras/requisicao-setores/", {
            "nome": "Manutenção Predial",
            "descricao": "Chamados internos",
            "pode_fazer_requisicao": True,
            "recebe_requisicoes": True,
            "controla_estoque_uso_consumo": False,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        setor_id = resp.data["id"]

        resp = self.client.patch(f"/api/compras/requisicao-setores/{setor_id}/", {
            "descricao": "Chamados e materiais internos",
            "controla_estoque_uso_consumo": True,
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["controla_estoque_uso_consumo"])

        resp = self.client.post(f"/api/compras/requisicao-setores/{setor_id}/inativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["ativo"])
        resp = self.client.post(f"/api/compras/requisicao-setores/{setor_id}/ativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["ativo"])

    def test_usuario_de_outra_empresa_nao_acessa_setor_e_empresa_invalida_e_rejeitada(self):
        self.client.force_authenticate(self.outro)
        resp = self.client.get(f"/api/compras/requisicao-setores/{self.setor.id}/")
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post("/api/compras/requisicao-setores/", {"empresa": self.empresa.id, "nome": "Outro"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_setor_usado_em_requisicao_mantem_integridade_e_inativo_sai_da_lista(self):
        req = self.criar_requisicao()
        self.assertEqual(req.setor_id, self.setor.id)
        self.client.post(f"/api/compras/requisicao-setores/{self.setor.id}/inativar/", {}, format="json")
        req.refresh_from_db()
        self.assertEqual(req.setor.nome, "Financeiro")
        resp = self.client.get("/api/compras/requisicao-setores/", {"ativo": "true", "pode_fazer_requisicao": "true"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(self.setor.id, [r["id"] for r in rows])

    def test_categoria_servico_de_outra_empresa_nao_pode_ser_usada(self):
        req = self.criar_requisicao()
        categoria_b = RequisicaoServicoCategoria.objects.create(empresa=self.empresa_b, nome="Serviços B")
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Manutenção",
            "descricao_servico": "Serviço corretivo",
            "categoria_servico": categoria_b.id,
            "tipo_servico": "CORRETIVA",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_material_cadastrado_item_livre_e_servico_sem_produto(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        livre = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Notebook administrativo",
            "categoria": "Equipamento",
            "finalidade": "IMOBILIZADO",
            "unidade": self.unidade.Idunidade,
            "qtd_solicitada": "1.000",
            "especificacao_tecnica": "16 GB RAM, SSD 512 GB",
        }, format="json")
        self.assertEqual(livre.status_code, 201, livre.data)
        servico = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Manutenção impressora",
            "descricao_servico": "Revisão geral",
            "categoria_servico": self.categoria.id,
            "tipo_servico": "REVISAO",
        }, format="json")
        self.assertEqual(servico.status_code, 201, servico.data)
        self.assertIsNone(servico.data["produto"])

    def test_material_cadastrado_usa_produto_uso_consumo_e_unidade_do_produto(self):
        req = self.criar_requisicao()
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.prod_uso_dec.Idproduto,
            "unidade": self.un_int.Idunidade,
            "finalidade": "ALMOXARIFADO",
            "qtd_solicitada": "1.500",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["unidade"], self.un_dec.Idunidade)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.prod_revenda.Idproduto,
            "finalidade": "USO_CONSUMO",
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_item_livre_permite_unidade_manual_e_rejeita_quantidade_zero_ou_negativa(self):
        req = self.criar_requisicao()
        livre = {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Item livre",
            "finalidade": "OUTRO",
            "unidade": self.un_dec.Idunidade,
            "qtd_solicitada": "1.500",
        }
        resp = self.client.post("/api/compras/requisicao-itens/", livre, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["unidade"], self.un_dec.Idunidade)
        for qtd in ("0.000", "-1.000"):
            data = {**livre, "descricao": f"Item livre {qtd}", "qtd_solicitada": qtd}
            resp = self.client.post("/api/compras/requisicao-itens/", data, format="json")
            self.assertEqual(resp.status_code, 400, resp.data)
            self.assertIn("qtd_solicitada", resp.data)

    def test_material_exige_finalidade_e_aceita_imobilizado_e_almoxarifado(self):
        req = self.criar_requisicao()
        base = {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Computador Dell",
            "categoria": "Informática",
            "unidade": self.unidade.Idunidade,
            "qtd_solicitada": "1.000",
            "especificacao_tecnica": "Intel i5, 8 GB RAM, SSD 500 GB",
        }
        resp = self.client.post("/api/compras/requisicao-itens/", base, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {**base, "finalidade": "IMOBILIZADO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["categoria"], "Informática")
        self.assertEqual(resp.data["finalidade"], "IMOBILIZADO")

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.produto.Idproduto,
            "finalidade": "ALMOXARIFADO",
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["finalidade"], "ALMOXARIFADO")

    def test_finalidade_invalida_e_servico_com_finalidade_sao_rejeitados(self):
        req = self.criar_requisicao()
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.produto.Idproduto,
            "finalidade": "PATRIMONIO",
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Manutenção",
            "descricao_servico": "Equipamento não está refrigerando.",
            "categoria_servico": self.categoria.id,
            "tipo_servico": "CORRETIVA",
            "finalidade": "USO_CONSUMO",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade", resp.data)

    def test_servico_exige_descricao_e_nao_precisa_campos_de_material(self):
        req = self.criar_requisicao()
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Manutenção de ar-condicionado",
            "categoria_servico": self.categoria.id,
            "tipo_servico": "CORRETIVA",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("descricao_servico", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Manutenção de ar-condicionado",
            "descricao_servico": "Equipamento da área de vendas não está refrigerando.",
            "categoria_servico": self.categoria.id,
            "tipo_servico": "CORRETIVA",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["finalidade"], "")

    def test_erros_de_item_retornam_mensagens_especificas(self):
        req = self.criar_requisicao()
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "finalidade": "USO_CONSUMO",
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("produto", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "finalidade": "USO_CONSUMO",
            "unidade": self.unidade.Idunidade,
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("descricao", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Livre sem unidade",
            "finalidade": "USO_CONSUMO",
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("unidade", resp.data)

    def test_salvar_rascunho_mantem_status_e_salvar_enviar_transiciona(self):
        req = self.criar_requisicao()
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Rascunho"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "RASCUNHO")
        self.item_produto(req)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/salvar-enviar/", {"requisicao": {"observacoes": "Enviar"}}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "AGUARDANDO_APROVACAO")

    def test_salvar_enviar_sem_item_falha_sem_alterar_status(self):
        req = self.criar_requisicao()
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/salvar-enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("itens", resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")

    def test_atendimento_estoque_so_para_material_cadastrado(self):
        req = self.criar_requisicao()
        livre = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Item livre",
            "finalidade": "USO_CONSUMO",
            "unidade": self.unidade.Idunidade,
            "qtd_solicitada": "1.000",
        }, format="json")
        servico = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": "Serviço",
            "descricao_servico": "Serviço corretivo",
            "categoria_servico": self.categoria.id,
            "tipo_servico": "CORRETIVA",
        }, format="json")
        self.assertEqual(livre.status_code, 201, livre.data)
        self.assertEqual(servico.status_code, 201, servico.data)
        self.aprovar(req)
        resp = self.client.post(f"/api/compras/requisicao-itens/{livre.data['id']}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{servico.data['id']}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_atendimento_integral_parcial_e_bloqueio_acima_saldo(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req, "5.000")
        self.aprovar(req)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(resp.data["qtd_pendente"]), Decimal("3.000"))
        self.assertEqual(resp.data["status"], "ATENDIDO_PARCIALMENTE")
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "4.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "3.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "ATENDIDO")
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(origem="REQUISICAO").count(), 2)

    def test_item_sem_estoque_pode_aguardar_cotacao(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req, "20.000")
        self.aprovar(req)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "20.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/aguardar-cotacao/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "AGUARDANDO_COTACAO")

    def test_multiplos_itens_status_individual(self):
        req = self.criar_requisicao()
        item_a = self.item_produto(req, "1.000")
        item_b = self.item_produto(req, "2.000")
        self.aprovar(req)
        self.client.post(f"/api/compras/requisicao-itens/{item_a}/atender/", {"quantidade": "1.000"}, format="json")
        self.client.post(f"/api/compras/requisicao-itens/{item_b}/aguardar-cotacao/", {}, format="json")
        statuses = set(Requisicao.objects.get(pk=req.pk).itens.values_list("status", flat=True))
        self.assertEqual(statuses, {"ATENDIDO", "AGUARDANDO_COTACAO"})

    def test_aprovacao_rejeicao_devolucao_transicoes(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/devolver/", {"motivo": "Ajustar"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "RASCUNHO")
        self.client.force_authenticate(self.solicitante)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/rejeitar/", {"motivo": "Sem necessidade"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "REJEITADA")

    def test_transicoes_invalidas_e_patch_status_sao_bloqueados_no_backend(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req)
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"status": "APROVADA"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")

        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/cancelar/", {"motivo": "Duplicada"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

        req2 = self.criar_requisicao()
        item2_id = self.item_produto(req2)
        self.client.post(f"/api/compras/requisicoes/{req2.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req2.id}/rejeitar/", {"motivo": "Não aprovado"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req2.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item2_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_atendimento_nao_permite_item_cancelado_rejeitado_ou_sem_pendente(self):
        req = self.criar_requisicao()
        cancelado_id = self.item_produto(req, "1.000")
        rejeitado_id = self.item_produto(req, "1.000")
        atendido_id = self.item_produto(req, "1.000")
        self.aprovar(req)
        RequisicaoItem.objects.filter(pk=cancelado_id).update(status="CANCELADO")
        RequisicaoItem.objects.filter(pk=rejeitado_id).update(status="REJEITADO")
        RequisicaoItem.objects.filter(pk=atendido_id).update(status="ATENDIDO", qtd_atendida=Decimal("1.000"), qtd_pendente=Decimal("0.000"))
        for item_id in (cancelado_id, rejeitado_id, atendido_id):
            resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "1.000"}, format="json")
            self.assertEqual(resp.status_code, 400, resp.data)

    def test_historico_e_auditoria_sao_gerados(self):
        with self.captureOnCommitCallbacks(execute=True):
            req = self.criar_requisicao()
            self.item_produto(req)
            self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertGreaterEqual(RequisicaoHistorico.objects.filter(requisicao=req).count(), 3)
        self.assertTrue(AuditLog.objects.filter(app_label="compras", model="requisicao", object_id=str(req.id)).exists())

    def test_api_impede_alterar_campos_protegidos_apos_envio(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"setor": "TI"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.patch(f"/api/compras/requisicao-itens/{item_id}/", {"qtd_solicitada": "9.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_pedido_nasce_sem_tipo_e_ignora_tipo_manual(self):
        pedido = self.criar_pedido(tipo="1")
        self.assertEqual(pedido.tipo, "")

    def test_primeiro_item_define_tipo_e_revenda_recalcula_qtd_no_backend(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido))
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "1")
        self.assertEqual(item.qtd, Decimal("6.000"))
        self.assertEqual(pedido.total_itens, Decimal("59.00"))
        self.assertEqual(pedido.total_pedido, Decimal("59.00"))

    def test_rejeita_produto_proprio_tipo_3(self):
        pedido = self.criar_pedido()
        resp = self.incluir_item(self.payload_uso(pedido, produto=self.prod_proprio), status=400)
        self.assertIn("produto", resp.data)

    def test_rejeita_todas_as_misturas_de_tipos(self):
        casos = (
            (self.payload_revenda, lambda p: self.payload_uso(p, produto=self.prod_uso)),
            (lambda p: self.payload_uso(p, produto=self.prod_uso), self.payload_revenda),
            (lambda p: self.payload_uso(p, produto=self.prod_uso), lambda p: self.payload_uso(p, produto=self.prod_insumo)),
            (lambda p: self.payload_uso(p, produto=self.prod_insumo), lambda p: self.payload_uso(p, produto=self.prod_uso)),
            (self.payload_revenda, lambda p: self.payload_uso(p, produto=self.prod_insumo)),
            (lambda p: self.payload_uso(p, produto=self.prod_insumo), self.payload_revenda),
        )
        for primeiro, segundo in casos:
            pedido = self.criar_pedido()
            self.incluir_item(primeiro(pedido))
            self.incluir_item(segundo(pedido), status=400)

    def test_excluir_ultimo_item_limpa_tipo_e_permite_redefinir(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido))
        resp = self.client.delete(f"/api/compras/itens/{item.id}/")
        self.assertEqual(resp.status_code, 204)
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "")
        self.assertEqual(pedido.total_pedido, Decimal("0.00"))
        item_uso = self.incluir_item(self.payload_uso(pedido))
        pedido.refresh_from_db()
        self.assertEqual(item_uso.produto.tipo_produto, "2")
        self.assertEqual(pedido.tipo, "2")

    def test_uso_consumo_e_insumo_respeitam_decimal_da_unidade(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="1.500"), status=400)
        item = self.incluir_item(self.payload_uso(pedido, produto=self.prod_uso_dec, qtd="1.500"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "2")
        self.assertEqual(item.qtd, Decimal("1.500"))

        pedido_insumo = self.criar_pedido()
        item_insumo = self.incluir_item(self.payload_uso(pedido_insumo, produto=self.prod_insumo, qtd="1.250"))
        pedido_insumo.refresh_from_db()
        self.assertEqual(pedido_insumo.tipo, "4")
        self.assertEqual(item_insumo.qtd, Decimal("1.250"))

    def test_operacoes_de_item_somente_em_aberto(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido))
        for status_pc in ("AP", "AT", "CA"):
            PedidoCompra.objects.filter(pk=pedido.pk).update(status=status_pc)
            self.assertEqual(self.client.post("/api/compras/itens/", self.payload_uso(pedido), format="json").status_code, 400)
            self.assertEqual(self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "3.000"}, format="json").status_code, 400)
            self.assertEqual(self.client.delete(f"/api/compras/itens/{item.id}/").status_code, 400)

    def test_totais_recalculam_em_inclusao_alteracao_exclusao_frete_e_desconto(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="10.00"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("20.00"))
        resp = self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "3.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", {"frete": "5.00", "total_desconto": "2.00"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("33.00"))
        self.assertEqual(self.client.delete(f"/api/compras/itens/{item.id}/").status_code, 204)
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("3.00"))

    def test_frete_e_desconto_rejeitam_negativos_e_total_final_negativo(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="1.000", preco_unit="10.00"))
        for payload in ({"frete": "-0.01"}, {"total_desconto": "-0.01"}, {"total_desconto": "10.01"}):
            resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", payload, format="json")
            self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", {"frete": "0.00", "total_desconto": "10.00"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_forma_pagamento_sincroniza_parcelas_apos_mudar_total(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="50.00"))
        self.configurar_forma(pedido)
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido).values_list("valor", flat=True)), [Decimal("50.00"), Decimal("50.00")])
        resp = self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "4.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido).values_list("valor", flat=True)), [Decimal("100.00"), Decimal("100.00")])

    def test_aprovacao_rejeita_sem_item_sem_forma_e_parcelas_divergentes(self):
        pedido = self.criar_pedido()
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.incluir_item(self.payload_uso(pedido))
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.configurar_forma(pedido)
        PedidoCompraParcela.objects.filter(pedido=pedido, parcela_n=1).update(valor=Decimal("1.00"))
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_aprovacao_valida_gera_financeiro_parcelas_e_auditoria(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="50.00"))
        self.configurar_forma(pedido)
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")
        pagar = Pagar.objects.get(pedido_compra=pedido.id)
        self.assertEqual(pagar.Valor_total, Decimal("100.00"))
        self.assertEqual(pagar.Idnatureza_id, self.natureza.idnatureza)
        self.assertEqual(PagarItem.objects.filter(Idpagar=pagar, status=PagarItem.STATUS_PREVISTO).count(), 2)
        self.assertEqual(PedidoCompraParcela.objects.filter(pedido=pedido, status="GERADA", pagar_item_id__isnull=False).count(), 2)

    def test_cancelamento_e_exclusao_somente_aberto(self):
        pedido = self.criar_pedido()
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/cancelar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.delete(f"/api/compras/pedidos/{pedido.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_multiempresa_bloqueia_loja_fornecedor_produto_pack_e_queryset(self):
        pedido_b = PedidoCompra.objects.create(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, observacoes="Pedido B")
        resp = self.client.get("/api/compras/pedidos/", {"empresa": self.empresa.id})
        pedidos = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(pedido_b.id, [p["id"] for p in pedidos])
        resp = self.client.post("/api/compras/pedidos/", {"loja": self.loja.id, "fornecedor": self.fornecedor_b.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        pedido = self.criar_pedido()
        prod_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Uso B", unidade=self.un_int)
        resp = self.client.post("/api/compras/itens/", self.payload_uso(pedido, produto=prod_b), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        pack_b = Pack.objects.create(empresa=self.empresa_b, nome="Pack B", grade=self.grade)
        resp = self.client.post("/api/compras/itens/", self.payload_revenda(pedido, pack=pack_b.id), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_recebimento_manual_parcial_total_smoke(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="10.000"))
        resp = self.client.post(
            "/api/compras/entregas/",
            {
                "item": item.id,
                "qtd_prevista": "10.000",
                "qtd_recebida": "4.000",
                "data_recebida": timezone.localdate().isoformat(),
                "status": "PARC",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.client.patch(f"/api/compras/entregas/{resp.data['id']}/", {"qtd_recebida": "10.000", "status": "RECB"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

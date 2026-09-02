from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import PerfilAcesso, PerfilModuloPermissao, PerfilProcessPermission, UserModulePermission
from accounts.services.effective_access import EffectiveAccessService
from cadastros.models import Empresa, EmpresaContrato, Fornecedor, Loja, ModuloSistema, Nat_Lancamento
from compras.models import Cotacao, CotacaoFornecedor, CotacaoItem, CotacaoProposta, CotacaoPropostaItem, CotacaoRequisicao, OrdemServico, OrdemServicoMaterial, PedidoCompra, PedidoCompraEntrega, PedidoCompraItem, PedidoCompraParcela, Requisicao, RequisicaoFinalidadeAquisicao, RequisicaoHistorico, RequisicaoItem, RequisicaoMaterialCategoria, RequisicaoMatrizResponsabilidade, RequisicaoServicoCategoria, RequisicaoSetor
from compras.serializers import CotacaoSerializer
from compras.views import CotacaoViewSet
from financeiro.models import FormaPagamento, FormaPagamentoParcela, Pagar, PagarItem, PrazoPagamento, PrazoPagamentoParcela
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem
from produto.models import Colecao, ConfigEan, Cor, Grade, Grupo, Pack, PackItem, Produto, ProdutoDetalhe, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao, Tamanho, Unidade
from auditoria.models import AuditLog


@override_settings(ALLOWED_HOSTS=["testserver"])
class CotacaoBaseTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.empresa = Empresa.objects.create(nome="Empresa Cot A", documento="33111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa Cot B", documento="33222222000191", plano_completo=True)
        self.mod_compras, _ = ModuloSistema.objects.get_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "ativo": True, "ordem": 10},
        )
        EmpresaContrato.objects.update_or_create(empresa=self.empresa, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        EmpresaContrato.objects.update_or_create(empresa=self.empresa_b, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Cot A", apelido_loja="Loja Cot A", cnpj="33111111000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja Cot B", apelido_loja="Loja Cot B", cnpj="33222222000100", estado="SP")
        self.user = User.objects.create_user("cotador", "cotador@test.local", "123", empresa=self.empresa, loja=self.loja)
        self.user_b = User.objects.create_user("cotador-b", "cotadorb@test.local", "123", empresa=self.empresa_b, loja=self.loja_b)
        self.perfil_compras = PerfilAcesso.objects.create(empresa=self.empresa, nome="Compras Cotação")
        PerfilModuloPermissao.objects.create(perfil=self.perfil_compras, modulo=self.mod_compras, acesso=UserModulePermission.Access.EDIT)
        self.user.perfil_principal = self.perfil_compras
        self.user.save(update_fields=["perfil_principal"])
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN", permite_decimal=False)
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B", Codigo="UNB", permite_decimal=False)
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Material cotado", unidade=self.unidade)
        self.setor = RequisicaoSetor.objects.create(empresa=self.empresa, nome="Compras")
        self.setor_b = RequisicaoSetor.objects.create(empresa=self.empresa_b, nome="Compras B")
        self.categoria_material = RequisicaoMaterialCategoria.objects.create(empresa=self.empresa, nome="Informática")
        self.categoria_limpeza = RequisicaoMaterialCategoria.objects.create(empresa=self.empresa, nome="Limpeza")

    def criar_cotacao(self, **extras):
        data = {
            "empresa": self.empresa,
            "loja": self.loja,
            "responsavel": self.user,
            "tipo_compra": "USO_CONSUMO",
        }
        data.update(extras)
        return Cotacao.objects.create(**data)

    def criar_fornecedor(self, documento="44999999000191", empresa=None, nome="Fornecedor Cot", ativo=True):
        documento = documento
        return Fornecedor.objects.create(
            empresa=empresa or self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="OUTROS",
            ativo=ativo,
        )

    def criar_requisicao(self, numero, empresa=None, loja=None, setor=None, user=None):
        empresa = empresa or self.empresa
        loja = loja or self.loja
        setor = setor or self.setor
        user = user or self.user
        return Requisicao.objects.create(numero=numero, empresa=empresa, loja=loja, setor=setor, requisitante=user, criado_por=user, justificativa=f"Req {numero}")

    def test_cria_cotacao_avulsa_com_defaults(self):
        cotacao = self.criar_cotacao()
        self.assertEqual(cotacao.numero, 1)
        self.assertEqual(cotacao.status, "EM_ELABORACAO")
        self.assertEqual(cotacao.prioridade, "NORMAL")
        self.assertEqual(cotacao.requisicoes_vinculadas.count(), 0)

    def test_cria_cotacao_com_empresa_e_loja_validas(self):
        cotacao = self.criar_cotacao(observacao="Cotação validada")
        cotacao.full_clean()
        self.assertEqual(cotacao.empresa, self.empresa)
        self.assertEqual(cotacao.loja, self.loja)

    def test_vincula_mais_de_uma_requisicao_na_mesma_cotacao(self):
        cotacao = self.criar_cotacao()
        req1 = self.criar_requisicao(1)
        req2 = self.criar_requisicao(2)
        CotacaoRequisicao.objects.create(cotacao=cotacao, requisicao=req1)
        CotacaoRequisicao.objects.create(cotacao=cotacao, requisicao=req2)
        self.assertEqual(set(cotacao.requisicoes_vinculadas.values_list("requisicao_id", flat=True)), {req1.id, req2.id})

    def test_cria_item_avulso(self):
        cotacao = self.criar_cotacao()
        item = CotacaoItem.objects.create(cotacao=cotacao, descricao="Item livre", quantidade_cotar=Decimal("3.000"), unidade=self.unidade, origem="AVULSO")
        self.assertEqual(item.origem, "AVULSO")
        self.assertIsNone(item.requisicao_item_origem)

    def test_cria_item_com_origem_em_requisicao(self):
        cotacao = self.criar_cotacao()
        req = self.criar_requisicao(1)
        req_item = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, descricao="Material", unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"))
        item = CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao="Material", quantidade_cotar=Decimal("5.000"), unidade=self.unidade, origem="REQUISICAO", requisicao_item_origem=req_item)
        self.assertEqual(item.origem, "REQUISICAO")
        self.assertEqual(item.requisicao_item_origem, req_item)

    def test_isolamento_basico_por_empresa_nos_vinculos(self):
        cotacao = self.criar_cotacao()
        req_b = self.criar_requisicao(1, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, user=self.user_b)
        vinculo = CotacaoRequisicao(cotacao=cotacao, requisicao=req_b)
        with self.assertRaises(ValidationError):
            vinculo.full_clean()

        req_item_b = RequisicaoItem.objects.create(requisicao=req_b, tipo="MATERIAL", origem="LIVRE", descricao="Outro", unidade=self.unidade_b, qtd_solicitada=Decimal("1.000"), qtd_pendente=Decimal("1.000"))
        item = CotacaoItem(cotacao=cotacao, descricao="Outro", quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="REQUISICAO", requisicao_item_origem=req_item_b)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_api_cria_cotacao_com_defaults(self):
        client = APIClient()
        client.force_authenticate(self.user)
        UserModulePermission.objects.create(user=self.user, modulo="compras", acesso=UserModulePermission.Access.EDIT)
        resp = client.post("/api/compras/cotacoes/", {"loja": self.loja.id, "tipo_compra": "USO_CONSUMO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["numero"], 1)
        self.assertEqual(resp.data["status"], "EM_ELABORACAO")
        self.assertEqual(resp.data["prioridade"], "NORMAL")
        self.assertEqual(resp.data["responsavel"], self.user.id)

    def test_cotacao_status_operacional_derivado(self):
        cotacao = self.criar_cotacao()
        self.assertEqual(CotacaoSerializer(cotacao).data["status_operacional"], "Em elaboração — sem itens")
        CotacaoItem.objects.create(cotacao=cotacao, descricao="Item", quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        self.assertEqual(CotacaoSerializer(cotacao).data["status_operacional"], "Aguardando fornecedores")
        fornecedor_a = self.criar_fornecedor()
        fornecedor_b = self.criar_fornecedor(documento="44888888000191", nome="Fornecedor B")
        participante_a = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor_a)
        CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor_b)
        self.assertEqual(CotacaoSerializer(cotacao).data["status_operacional"], "Aguardando propostas — 0 de 2 recebidas")
        participante_a.status_participacao = "PROPOSTA_RECEBIDA"
        participante_a.save(update_fields=["status_participacao"])
        self.assertEqual(CotacaoSerializer(cotacao).data["status_operacional"], "Aguardando propostas — 1 de 2 recebidas")
        cotacao.status = "CANCELADA"
        cotacao.save(update_fields=["status"])
        self.assertEqual(CotacaoSerializer(cotacao).data["status_operacional"], "Cancelada")

    def test_pedido_gerado_herda_pagamento_e_prazo_da_proposta(self):
        cotacao = self.criar_cotacao()
        item = CotacaoItem.objects.create(cotacao=cotacao, descricao="Item", quantidade_cotar=Decimal("2.000"), unidade=self.unidade, origem="AVULSO")
        fornecedor = self.criar_fornecedor()
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor, status_participacao="PROPOSTA_RECEBIDA")
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30D", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo="BOL", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta = CotacaoProposta.objects.create(
            cotacao=cotacao,
            cotacao_fornecedor=participante,
            forma_pagamento=forma.codigo,
            prazo_pagamento=prazo,
            condicao_pagamento="30 dias",
            prazo_entrega_dias=15,
            prazo_entrega="15",
        )
        CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=item, quantidade_ofertada=Decimal("2.000"), preco_unitario=Decimal("10.00"))
        proposta.recomputar_totais()
        proposta.save(update_fields=["total_itens", "total_proposta"])
        cotacao.proposta_vencedora = proposta
        view = CotacaoViewSet()
        cotacao.snapshot_proposta_aprovada = view._snapshot_proposta(proposta)
        cotacao.save(update_fields=["proposta_vencedora", "snapshot_proposta_aprovada"])
        request = APIRequestFactory().post("/")
        request.user = self.user
        pedido = view._gerar_pedido_da_cotacao(cotacao, request)
        self.assertEqual(pedido.prazo_pagamento, prazo)
        self.assertEqual(pedido.forma_pagamento, forma.codigo)
        self.assertEqual(pedido.itens.get().unidade, self.unidade)
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido).values_list("valor", flat=True)), [pedido.total_pedido])
        self.assertEqual(pedido.previsao_entrega, pedido.emissao + timedelta(days=15))

    def test_api_bloqueia_loja_fora_do_escopo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        UserModulePermission.objects.create(user=self.user, modulo="compras", acesso=UserModulePermission.Access.EDIT)
        resp = client.post("/api/compras/cotacoes/", {"loja": self.loja_b.id, "tipo_compra": "USO_CONSUMO"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_can_access_store_usuario_com_loja_permitida_e_nao_permitida(self):
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Sem Permissão", apelido_loja="Loja Sem Permissão", cnpj="33111111000290", estado="SP")
        service = EffectiveAccessService(self.user)
        self.assertTrue(service.can_access_store(self.loja))
        self.assertFalse(service.can_access_store(loja_extra))

    def test_can_access_store_admin_sem_lojas_marcadas_acessa_empresa_e_bloqueia_outra(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Admin Cot", apelido_loja="Loja Admin Cot", cnpj="33111111000371", estado="SP")
        admin = User.objects.create_user("admin-cot", "admincot@test.local", "123", empresa=self.empresa, loja=self.loja, type="Admin")
        admin.lojas.clear()
        service = EffectiveAccessService(admin)
        self.assertTrue(service.can_access_store(self.loja))
        self.assertTrue(service.can_access_store(loja_extra))
        self.assertFalse(service.can_access_store(self.loja_b))

    def test_can_access_store_company_master_sem_lojas_marcadas_acessa_empresa(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Master Cot", apelido_loja="Loja Master Cot", cnpj="33111111000452", estado="SP")
        master = User.objects.create_user("master-cot", "mastercot@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        master.lojas.clear()
        contrato = self.empresa.contrato
        contrato.usuario_master = master
        contrato.save(update_fields=["usuario_master", "updated_at"])
        service = EffectiveAccessService(master)
        self.assertTrue(service.can_access_store(loja_extra))
        self.assertFalse(service.can_access_store(self.loja_b))

    def test_can_access_store_superuser_preservado(self):
        User = get_user_model()
        superuser = User.objects.create_superuser("super-cot", "supercot@test.local", "123")
        service = EffectiveAccessService(superuser)
        self.assertTrue(service.can_access_store(self.loja))
        self.assertTrue(service.can_access_store(self.loja_b))

    def test_api_admin_cria_cotacao_em_loja_da_empresa_sem_loja_marcada(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Admin API", apelido_loja="Loja Admin API", cnpj="33111111000533", estado="SP")
        admin = User.objects.create_user("admin-cot-api", "admincotapi@test.local", "123", empresa=self.empresa, loja=self.loja, type="Admin")
        admin.lojas.clear()
        admin.perfil_principal = self.perfil_compras
        admin.save(update_fields=["perfil_principal"])
        client = APIClient()
        client.force_authenticate(admin)
        resp = client.post("/api/compras/cotacoes/", {"loja": loja_extra.id, "tipo_compra": "USO_CONSUMO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["loja"], loja_extra.id)

    def test_api_admin_cria_item_e_listagem_da_cotacao_retorna_item(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Admin Item", apelido_loja="Loja Admin Item", cnpj="33111111000886", estado="SP")
        admin = User.objects.create_user("admin-cot-item", "admincotitem@test.local", "123", empresa=self.empresa, loja=self.loja, type="Admin")
        admin.lojas.clear()
        admin.perfil_principal = self.perfil_compras
        admin.save(update_fields=["perfil_principal"])
        client = APIClient()
        client.force_authenticate(admin)
        resp = client.post("/api/compras/cotacoes/", {"loja": loja_extra.id, "tipo_compra": "USO_CONSUMO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        cotacao_id = resp.data["id"]
        resp = client.post("/api/compras/cotacao-itens/", {
            "cotacao": cotacao_id,
            "produto": self.produto.pkproduto,
            "quantidade_cotar": "1.000",
            "unidade": self.unidade.Idunidade,
            "origem": "AVULSO",
            "descricao": "",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        item_id = resp.data["id"]
        self.assertTrue(CotacaoItem.objects.filter(pk=item_id, cotacao_id=cotacao_id, cotacao__empresa=self.empresa, cotacao__loja=loja_extra, produto=self.produto, unidade=self.unidade, quantidade_cotar=Decimal("1.000"), origem="AVULSO").exists())
        resp = client.get("/api/compras/cotacao-itens/", {"cotacao": cotacao_id})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data if isinstance(resp.data, list) else resp.data["results"]
        self.assertIn(item_id, [row["id"] for row in rows])
        resp = client.get(f"/api/compras/cotacoes/{cotacao_id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn(item_id, [row["id"] for row in resp.data["itens"]])

    def test_api_admin_adiciona_fornecedor_e_listagem_da_cotacao_retorna_fornecedor(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Admin Forn", apelido_loja="Loja Admin Forn", cnpj="33111111000967", estado="SP")
        admin = User.objects.create_user("admin-cot-forn", "admincotforn@test.local", "123", empresa=self.empresa, loja=self.loja, type="Admin")
        admin.lojas.clear()
        admin.perfil_principal = self.perfil_compras
        admin.save(update_fields=["perfil_principal"])
        fornecedor = self.criar_fornecedor(documento="44555555000191", nome="Fornecedor Listagem")
        fornecedor_b = self.criar_fornecedor(documento="44555555000272", empresa=self.empresa_b, nome="Fornecedor Outra Empresa")
        cotacao_outra = self.criar_cotacao(empresa=self.empresa_b, loja=self.loja_b, responsavel=self.user_b)
        participante_outra = CotacaoFornecedor.objects.create(cotacao=cotacao_outra, fornecedor=fornecedor_b)
        client = APIClient()
        client.force_authenticate(admin)
        resp = client.post("/api/compras/cotacoes/", {"loja": loja_extra.id, "tipo_compra": "USO_CONSUMO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        cotacao_id = resp.data["id"]
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao_id, "fornecedor": fornecedor.id}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        participante_id = resp.data["id"]
        self.assertTrue(CotacaoFornecedor.objects.filter(pk=participante_id, cotacao_id=cotacao_id, fornecedor=fornecedor, status_participacao="CONVIDADO").exists())
        resp = client.get("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao_id})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data if isinstance(resp.data, list) else resp.data["results"]
        ids = [row["id"] for row in rows]
        self.assertIn(participante_id, ids)
        self.assertNotIn(participante_outra.id, ids)
        resp = client.get(f"/api/compras/cotacoes/{cotacao_id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = client.get("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao_id})
        rows = resp.data if isinstance(resp.data, list) else resp.data["results"]
        self.assertIn(participante_id, [row["id"] for row in rows])

    def test_api_edita_em_elaboracao_e_bloqueia_fora_dela(self):
        client = APIClient()
        client.force_authenticate(self.user)
        UserModulePermission.objects.create(user=self.user, modulo="compras", acesso=UserModulePermission.Access.EDIT)
        cotacao = self.criar_cotacao()
        resp = client.patch(f"/api/compras/cotacoes/{cotacao.id}/", {"observacao": "Editada"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["observacao"], "Editada")
        cotacao.status = "ABERTA"
        cotacao.save(update_fields=["status"])
        resp = client.patch(f"/api/compras/cotacoes/{cotacao.id}/", {"observacao": "Bloqueada"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_fornecedor_adiciona_valido(self):
        client = APIClient()
        client.force_authenticate(self.user)
        fornecedor = self.criar_fornecedor()
        cotacao = self.criar_cotacao()
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao.id, "fornecedor": fornecedor.id}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status_participacao"], "CONVIDADO")
        self.assertEqual(resp.data["fornecedor"], fornecedor.id)

    def test_api_cotacao_fornecedor_impede_duplicado(self):
        client = APIClient()
        client.force_authenticate(self.user)
        fornecedor = self.criar_fornecedor()
        cotacao = self.criar_cotacao()
        CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor)
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao.id, "fornecedor": fornecedor.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_fornecedor_impede_outra_empresa_e_inativo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        fornecedor_b = self.criar_fornecedor(documento="44888888000191", empresa=self.empresa_b, nome="Fornecedor B")
        inativo = self.criar_fornecedor(documento="44777777000191", nome="Fornecedor Inativo", ativo=False)
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao.id, "fornecedor": fornecedor_b.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao.id, "fornecedor": inativo.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_fornecedor_desclassificacao_exige_motivo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        participante = CotacaoFornecedor.objects.create(cotacao=self.criar_cotacao(), fornecedor=self.criar_fornecedor())
        resp = client.patch(f"/api/compras/cotacao-fornecedores/{participante.id}/", {"status_participacao": "DESCLASSIFICADO", "motivo_desclassificacao": ""}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-fornecedores/{participante.id}/", {"status_participacao": "DESCLASSIFICADO", "motivo_desclassificacao": "Sem aderência"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_api_cotacao_fornecedor_remove_em_fase_editavel(self):
        client = APIClient()
        client.force_authenticate(self.user)
        participante = CotacaoFornecedor.objects.create(cotacao=self.criar_cotacao(status="ABERTA"), fornecedor=self.criar_fornecedor())
        resp = client.delete(f"/api/compras/cotacao-fornecedores/{participante.id}/")
        self.assertEqual(resp.status_code, 204, resp.data)
        self.assertFalse(CotacaoFornecedor.objects.filter(pk=participante.pk).exists())

    def test_api_cotacao_fornecedor_bloqueia_status_final_da_cotacao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao(status="APROVADA")
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        fornecedor_novo = self.criar_fornecedor(documento="44666666000191", nome="Fornecedor Novo")
        resp = client.post("/api/compras/cotacao-fornecedores/", {"cotacao": cotacao.id, "fornecedor": fornecedor_novo.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-fornecedores/{participante.id}/", {"status_participacao": "RECUSOU"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.delete(f"/api/compras/cotacao-fornecedores/{participante.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def criar_item_cotacao(self, cotacao=None, descricao="Item proposta"):
        return CotacaoItem.objects.create(
            cotacao=cotacao or self.criar_cotacao(),
            produto=self.produto,
            descricao=descricao,
            quantidade_cotar=Decimal("5.000"),
            unidade=self.unidade,
            origem="AVULSO",
        )

    def proposta_payload(self, cotacao, participante, item, itens=None, **extras):
        data = {
            "cotacao": cotacao.id,
            "cotacao_fornecedor": participante.id,
            "frete": "10.00",
            "outras_despesas": "5.00",
            "desconto_geral": "3.00",
            "itens": itens if itens is not None else [{
                "cotacao_item": item.id,
                "quantidade_ofertada": "2.000",
                "preco_unitario": "10.00",
                "desconto_item": "1.00",
                "marca": "Marca A",
            }],
        }
        data.update(extras)
        return data

    def test_api_cotacao_proposta_cria_valida_totais_e_status_fornecedor(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante, item), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Decimal(resp.data["total_itens"]), Decimal("19.00"))
        self.assertEqual(Decimal(resp.data["total_proposta"]), Decimal("31.00"))
        participante.refresh_from_db()
        self.assertEqual(participante.status_participacao, "PROPOSTA_RECEBIDA")

    def test_api_cotacao_proposta_guarda_forma_prazo_pagamento_e_entrega_separados(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30DPROP", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo="BOLPROP", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        resp = client.post(
            "/api/compras/cotacao-propostas/",
            self.proposta_payload(cotacao, participante, item, forma_pagamento=forma.codigo, prazo_pagamento=prazo.Idprazo, prazo_entrega_dias=15),
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["forma_pagamento"], forma.codigo)
        self.assertEqual(resp.data["forma_pagamento_descricao"], "Boleto")
        self.assertEqual(resp.data["prazo_pagamento"], prazo.Idprazo)
        self.assertEqual(resp.data["prazo_pagamento_descricao"], "30 dias")
        self.assertEqual(resp.data["prazo_entrega_dias"], 15)
        self.assertNotEqual(resp.data["forma_pagamento"], "30 dias")

        resp = client.get(f"/api/compras/cotacao-propostas/{resp.data['id']}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["forma_pagamento"], forma.codigo)
        self.assertEqual(resp.data["prazo_pagamento"], prazo.Idprazo)
        self.assertEqual(resp.data["prazo_entrega_dias"], 15)

    def test_api_cotacao_proposta_exige_fornecedor_participante(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        participante_b = CotacaoFornecedor.objects.create(cotacao=self.criar_cotacao(), fornecedor=self.criar_fornecedor(documento="44555555000191", nome="Fornecedor Outro"))
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante_b, item), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_proposta_item_deve_pertencer_a_cotacao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item_outra = self.criar_item_cotacao(self.criar_cotacao(), "Outro")
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante, item_outra), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_proposta_quantidade_e_preco_validos(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante, item, itens=[{"cotacao_item": item.id, "quantidade_ofertada": "0.000", "preco_unitario": "10.00"}]), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante, item, itens=[{"cotacao_item": item.id, "quantidade_ofertada": "1.000", "preco_unitario": "-1.00"}]), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_proposta_permite_item_sem_oferta(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        self.criar_item_cotacao(cotacao, "Sem oferta")
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        resp = client.post("/api/compras/cotacao-propostas/", self.proposta_payload(cotacao, participante, item), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(len(resp.data["itens"]), 1)

    def test_api_cotacao_proposta_edicao_permitida_em_fase_correta(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao(status="ABERTA")
        item = self.criar_item_cotacao(cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante, frete=Decimal("1.00"))
        CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=item, quantidade_ofertada=Decimal("1.000"), preco_unitario=Decimal("2.00"))
        proposta.recomputar_totais()
        proposta.save(update_fields=["total_itens", "total_proposta"])
        resp = client.patch(f"/api/compras/cotacao-propostas/{proposta.id}/", {"frete": "3.00"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        cotacao.status = "APROVADA"
        cotacao.save(update_fields=["status"])
        resp = client.patch(f"/api/compras/cotacao-propostas/{proposta.id}/", {"frete": "4.00"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def criar_proposta_com_item(self, cotacao, item, documento, preco, qtd="1.000", frete="0.00"):
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor(documento=documento, nome=f"Fornecedor {documento[-4:]}"))
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante, frete=Decimal(frete))
        CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=item, quantidade_ofertada=Decimal(qtd), preco_unitario=Decimal(preco))
        proposta.recomputar_totais()
        proposta.save(update_fields=["total_itens", "total_proposta"])
        return proposta

    def criar_aprovador_cotacao(self):
        User = get_user_model()
        aprovador = User.objects.create_user("aprovador-cot", "aprovadorcot@test.local", "123", empresa=self.empresa, loja=self.loja)
        perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome="Aprovador Cotação")
        PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.mod_compras, acesso=UserModulePermission.Access.EDIT)
        PerfilProcessPermission.objects.create(perfil=perfil, codigo="cotacao.aprovar", permitido=True)
        aprovador.perfil_principal = perfil
        aprovador.save(update_fields=["perfil_principal"])
        return aprovador

    def test_api_cotacao_comparativo_duas_propostas_menor_preco_total_e_percentual(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        self.criar_proposta_com_item(cotacao, item, "44111111000191", "10.00")
        self.criar_proposta_com_item(cotacao, item, "44222222000191", "12.00")
        resp = client.get(f"/api/compras/cotacoes/{cotacao.id}/comparativo/")
        self.assertEqual(resp.status_code, 200, resp.data)
        propostas = resp.data["propostas"]
        self.assertEqual(len(propostas), 2)
        self.assertTrue(propostas[0]["itens"][0]["menor_preco_unitario"])
        self.assertTrue(propostas[0]["menor_total_geral"])
        self.assertEqual(Decimal(str(propostas[1]["diferenca_percentual"])), Decimal("20.00"))
        self.assertEqual(Decimal(str(propostas[0]["economia_vs_mais_cara"])), Decimal("2.00"))

    def test_api_cotacao_comparativo_item_sem_oferta_nao_vira_zero(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item_a = self.criar_item_cotacao(cotacao, "Item A")
        self.criar_item_cotacao(cotacao, "Item B")
        self.criar_proposta_com_item(cotacao, item_a, "44333333000191", "10.00")
        resp = client.get(f"/api/compras/cotacoes/{cotacao.id}/comparativo/")
        self.assertEqual(resp.status_code, 200, resp.data)
        sem_oferta = resp.data["propostas"][0]["itens"][1]
        self.assertTrue(sem_oferta["sem_oferta"])
        self.assertIsNone(sem_oferta["preco_unitario"])
        self.assertIsNone(sem_oferta["custo_final_item"])

    def test_api_cotacao_comparativo_nao_mistura_outra_cotacao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        outra = self.criar_cotacao()
        item_outra = self.criar_item_cotacao(outra, "Outra cotação")
        self.criar_proposta_com_item(cotacao, item, "44444444000191", "10.00")
        self.criar_proposta_com_item(outra, item_outra, "44555555000191", "1.00")
        resp = client.get(f"/api/compras/cotacoes/{cotacao.id}/comparativo/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data["propostas"]), 1)
        self.assertNotEqual(resp.data["propostas"][0]["itens"][0]["descricao"], "Outra cotação")

    def test_api_cotacao_selecao_manual_somente_um_vencedor(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        p1 = self.criar_proposta_com_item(cotacao, item, "44611111000191", "10.00")
        p2 = self.criar_proposta_com_item(cotacao, item, "44622222000191", "9.00")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": p1.id, "justificativa": "Prazo melhor"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": p2.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.proposta_vencedora_id, p2.id)

    def test_api_cotacao_vencedor_justificativa_obrigatoria(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        p1 = self.criar_proposta_com_item(cotacao, item, "44633333000191", "10.00")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": p1.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        p2 = self.criar_proposta_com_item(cotacao, item, "44644444000191", "9.00")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": p1.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": p1.id, "justificativa": "Entrega imediata"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_api_cotacao_envio_sem_vencedor_bloqueado(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        self.criar_proposta_com_item(cotacao, item, "44655555000191", "10.00")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/enviar-aprovacao/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_aprovacao_permissao_snapshot_e_imutabilidade(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        proposta = self.criar_proposta_com_item(cotacao, item, "44666666000191", "10.00")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": proposta.id, "justificativa": "Única proposta"}, format="json")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/enviar-aprovacao/", {}, format="json")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)
        client.force_authenticate(self.criar_aprovador_cotacao())
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "PEDIDO_GERADO")
        self.assertIsNotNone(resp.data["snapshot_proposta_aprovada"])
        resp = client.patch(f"/api/compras/cotacoes/{cotacao.id}/", {"observacao": "Não pode"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-propostas/{proposta.id}/", {"frete": "99.00"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cotacao_rejeicao_exige_motivo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        proposta = self.criar_proposta_com_item(cotacao, item, "44677777000191", "10.00")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": proposta.id, "justificativa": "Única proposta"}, format="json")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/enviar-aprovacao/", {}, format="json")
        client.force_authenticate(self.criar_aprovador_cotacao())
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/rejeitar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/rejeitar/", {"motivo": "Revisar valores"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "REJEITADA")

    def aprovar_cotacao_com_pedido(self, frete="7.00"):
        client = APIClient()
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        proposta = self.criar_proposta_com_item(cotacao, item, "44688888000191", "12.00", qtd="2.000", frete=frete)
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo=f"30D{cotacao.id}", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo=f"BOL{cotacao.id}", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta.outras_despesas = Decimal("4.00")
        proposta.desconto_geral = Decimal("3.00")
        proposta.forma_pagamento = forma.codigo
        proposta.prazo_pagamento = prazo
        proposta.condicao_pagamento = "30 dias"
        proposta.prazo_entrega_dias = 15
        proposta.prazo_entrega = "15"
        proposta.recomputar_totais()
        proposta.save(update_fields=["outras_despesas", "desconto_geral", "forma_pagamento", "prazo_pagamento", "condicao_pagamento", "prazo_entrega_dias", "prazo_entrega", "total_itens", "total_proposta"])
        client.force_authenticate(self.user)
        client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": proposta.id, "justificativa": "Única proposta"}, format="json")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/enviar-aprovacao/", {}, format="json")
        client.force_authenticate(self.criar_aprovador_cotacao())
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/aprovar/", {}, format="json")
        cotacao.refresh_from_db()
        return client, cotacao, proposta, resp

    def test_api_cotacao_aprovacao_gera_um_pedido_com_dados_do_snapshot(self):
        client, cotacao, proposta, resp = self.aprovar_cotacao_com_pedido()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "PEDIDO_GERADO")
        pedido = cotacao.pedido_compra_gerado
        self.assertEqual(pedido.cotacao_origem, cotacao)
        self.assertEqual(pedido.fornecedor, proposta.cotacao_fornecedor.fornecedor)
        self.assertEqual(pedido.loja, cotacao.loja)
        self.assertEqual(pedido.forma_pagamento, proposta.forma_pagamento)
        self.assertNotEqual(pedido.forma_pagamento, "30 dias")
        self.assertEqual(pedido.prazo_pagamento, proposta.prazo_pagamento)
        self.assertEqual(pedido.itens.get().unidade, self.unidade)
        self.assertEqual(pedido.previsao_entrega, pedido.emissao + timedelta(days=15))
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido, status="PLAN").values_list("valor", flat=True)), [pedido.total_pedido])
        snapshot = cotacao.snapshot_proposta_aprovada
        self.assertEqual(snapshot["forma_pagamento"], proposta.forma_pagamento)
        self.assertEqual(snapshot["forma_pagamento_legivel"], "Boleto")
        self.assertEqual(snapshot["prazo_pagamento"], proposta.prazo_pagamento_id)
        self.assertEqual(snapshot["prazo_pagamento_legivel"], "30 dias")
        self.assertEqual(snapshot["prazo_entrega_dias"], 15)
        self.assertEqual(resp.data["forma_pagamento_vencedora"], proposta.forma_pagamento)
        self.assertEqual(resp.data["prazo_pagamento_vencedor"], proposta.prazo_pagamento_id)
        self.assertEqual(resp.data["prazo_entrega_vencedor_dias"], 15)
        self.assertEqual(pedido.frete, Decimal("7.00"))
        self.assertEqual(pedido.outras_despesas, Decimal("4.00"))
        self.assertEqual(pedido.total_desconto, Decimal("3.00"))
        self.assertEqual(pedido.total_pedido, Decimal("32.00"))
        item = pedido.itens.get()
        self.assertEqual(item.qtd, Decimal("2.000"))
        self.assertEqual(item.preco_unit, Decimal("12.00"))

        natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo=f"COT{cotacao.id}",
            categoria_principal="Compras",
            subcategoria="Pedido",
            descricao="Compra por cotação",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        resp = client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")
        self.assertEqual(PedidoCompraParcela.objects.filter(pedido=pedido, status="GERADA", pagar_item_id__isnull=False).count(), 1)

    def test_api_cotacao_aprovacao_nao_duplica_pedido(self):
        client, cotacao, _proposta, resp = self.aprovar_cotacao_com_pedido()
        self.assertEqual(resp.status_code, 200, resp.data)
        first_id = cotacao.pedido_compra_gerado.id
        cotacao.status = "AGUARDANDO_APROVACAO"
        cotacao.save(update_fields=["status"])
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(PedidoCompra.objects.filter(cotacao_origem=cotacao).count(), 1)
        self.assertEqual(cotacao.pedido_compra_gerado.id, first_id)

    def test_api_pedido_originado_de_cotacao_bloqueia_alteracao_comercial(self):
        client, cotacao, _proposta, resp = self.aprovar_cotacao_com_pedido()
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido = cotacao.pedido_compra_gerado
        item = pedido.itens.get()
        resp = client.patch(f"/api/compras/pedidos/{pedido.id}/", {"frete": "99.00"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "9.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.delete(f"/api/compras/itens/{item.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_falha_geracao_pedido_faz_rollback(self):
        client = APIClient()
        cotacao = self.criar_cotacao()
        item = self.criar_item_cotacao(cotacao)
        proposta = self.criar_proposta_com_item(cotacao, item, "44699999000191", "12.00")
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30DFALHA", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo="BOLFALHA", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta.forma_pagamento = forma.codigo
        proposta.prazo_pagamento = prazo
        proposta.condicao_pagamento = prazo.descricao
        proposta.save(update_fields=["forma_pagamento", "prazo_pagamento", "condicao_pagamento"])
        client.force_authenticate(self.user)
        client.post(f"/api/compras/cotacoes/{cotacao.id}/selecionar-vencedor/", {"proposta": proposta.id, "justificativa": "Única proposta"}, format="json")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/enviar-aprovacao/", {}, format="json")
        client.force_authenticate(self.criar_aprovador_cotacao())
        with patch("compras.views.PedidoCompraItem.objects.create", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                client.post(f"/api/compras/cotacoes/{cotacao.id}/aprovar/", {}, format="json")
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.status, "AGUARDANDO_APROVACAO")
        self.assertFalse(PedidoCompra.objects.filter(cotacao_origem=cotacao).exists())

    def test_api_cancela_cotacao_antes_aprovacao_e_exige_motivo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/cancelar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/cancelar/", {"motivo": "Compra suspensa"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "CANCELADA")
        self.assertEqual(resp.data["motivo_cancelamento"], "Compra suspensa")
        self.assertTrue(AuditLog.objects.filter(model="cotacao", object_id=str(cotacao.pk)).exists())

    def test_api_cotacao_cancelada_fica_imutavel(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao(status="CANCELADA")
        item = self.criar_item_cotacao(cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.criar_fornecedor())
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante)
        resp = client.patch(f"/api/compras/cotacoes/{cotacao.id}/", {"observacao": "Não altera"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-itens/{item.id}/", {"quantidade_cotar": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-fornecedores/{participante.id}/", {"observacao": "Não altera"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.patch(f"/api/compras/cotacao-propostas/{proposta.id}/", {"frete": "1.00"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_cancela_aprovada_sem_pedido_executado(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao(status="APROVADA")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/cancelar/", {"motivo": "Revisão"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "CANCELADA")
        self.assertTrue(Cotacao.objects.filter(pk=cotacao.pk).exists())

    def test_api_cancela_pedido_gerado_sem_recebimento_preserva_registros(self):
        client, cotacao, _proposta, resp = self.aprovar_cotacao_com_pedido()
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido = cotacao.pedido_compra_gerado
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/cancelar/", {"motivo": "Cancelamento aprovado"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        cotacao.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(cotacao.status, "CANCELADA")
        self.assertEqual(pedido.status, "CA")
        self.assertTrue(PedidoCompra.objects.filter(pk=pedido.pk).exists())

    def test_api_bloqueia_cancelamento_com_pedido_recebido(self):
        client, cotacao, _proposta, resp = self.aprovar_cotacao_com_pedido()
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido = cotacao.pedido_compra_gerado
        item = pedido.itens.get()
        PedidoCompraEntrega.objects.create(item=item, qtd_prevista=item.qtd, qtd_recebida=Decimal("1.000"), status="PARC")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/cancelar/", {"motivo": "Cancelar"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        cotacao.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(cotacao.status, "PEDIDO_GERADO")
        self.assertEqual(pedido.status, "AB")

    def test_api_item_produto_cadastrado_valido(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        resp = client.post("/api/compras/cotacao-itens/", {"cotacao": cotacao.id, "origem": "AVULSO", "produto": self.produto.pkproduto, "quantidade_cotar": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["descricao"], self.produto.descricao)
        self.assertEqual(resp.data["unidade"], self.unidade.Idunidade)

    def test_api_item_avulso_valido(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        resp = client.post("/api/compras/cotacao-itens/", {"cotacao": cotacao.id, "origem": "AVULSO", "descricao": "Item livre", "quantidade_cotar": "1.500", "unidade": self.unidade.Idunidade}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["descricao"], "Item livre")

    def test_api_bloqueia_quantidade_menor_ou_igual_zero(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        resp = client.post("/api/compras/cotacao-itens/", {"cotacao": cotacao.id, "origem": "AVULSO", "descricao": "Item livre", "quantidade_cotar": "0.000", "unidade": self.unidade.Idunidade}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_bloqueia_origem_requisicao_sem_item_origem(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        resp = client.post("/api/compras/cotacao-itens/", {"cotacao": cotacao.id, "origem": "REQUISICAO", "descricao": "Item req", "quantidade_cotar": "1.000", "unidade": self.unidade.Idunidade}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_bloqueia_edicao_e_exclusao_item_fora_de_elaboracao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = CotacaoItem.objects.create(cotacao=cotacao, descricao="Item livre", quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        cotacao.status = "ABERTA"
        cotacao.save(update_fields=["status"])
        resp = client.patch(f"/api/compras/cotacao-itens/{item.id}/", {"observacao": "bloqueia"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = client.delete(f"/api/compras/cotacao-itens/{item.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def _req_aprovada_com_itens(self, numero, loja=None):
        req = self.criar_requisicao(numero, loja=loja or self.loja)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item1 = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, descricao="Material", unidade=self.unidade, qtd_solicitada=Decimal("2.000"), qtd_pendente=Decimal("2.000"))
        item2 = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="LIVRE", descricao="Livre", unidade=self.unidade, qtd_solicitada=Decimal("3.000"), qtd_pendente=Decimal("3.000"))
        return req, [item1, item2]

    def _req_aprovada_com_produto(self, numero, qtd, categoria=None, loja=None, produto=None):
        req = self.criar_requisicao(numero, loja=loja or self.loja)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item = RequisicaoItem.objects.create(
            requisicao=req,
            tipo="MATERIAL",
            origem="PRODUTO",
            produto=produto or self.produto,
            descricao=(produto or self.produto).descricao,
            unidade=self.unidade,
            categoria_material=categoria or self.categoria_material,
            qtd_solicitada=qtd,
            qtd_pendente=qtd,
        )
        return req, item

    def test_api_cotacao_sem_requisicao_continua_valida(self):
        cotacao = self.criar_cotacao()
        self.assertEqual(cotacao.requisicoes_vinculadas.count(), 0)
        self.assertEqual(cotacao.itens.count(), 0)

    def test_api_adiciona_uma_requisicao_e_copia_todos_itens_com_origem(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        req, req_itens = self._req_aprovada_com_itens(10)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req.id]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(CotacaoRequisicao.objects.filter(cotacao=cotacao, requisicao=req).count(), 1)
        itens = list(CotacaoItem.objects.filter(cotacao=cotacao, origem="REQUISICAO"))
        self.assertEqual(len(itens), 2)
        self.assertEqual({i.requisicao_item_origem_id for i in itens}, {i.id for i in req_itens})

    def test_api_adiciona_varias_requisicoes(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        req1, _ = self._req_aprovada_com_itens(11)
        req2, _ = self._req_aprovada_com_itens(12)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req1.id, req2.id]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(cotacao.requisicoes_vinculadas.count(), 2)
        self.assertEqual(cotacao.itens.filter(origem="REQUISICAO").count(), 4)

    def test_api_bloqueia_duplicidade_de_requisicao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        req, _ = self._req_aprovada_com_itens(13)
        client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req.id]}, format="json")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req.id]}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_remove_requisicao_remove_somente_itens_da_requisicao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        req, _ = self._req_aprovada_com_itens(14)
        avulso = CotacaoItem.objects.create(cotacao=cotacao, descricao="Avulso", quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req.id]}, format="json")
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/remover-requisicao/", {"requisicao": req.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(CotacaoRequisicao.objects.filter(cotacao=cotacao, requisicao=req).exists())
        self.assertFalse(CotacaoItem.objects.filter(cotacao=cotacao, origem="REQUISICAO").exists())
        self.assertTrue(CotacaoItem.objects.filter(pk=avulso.pk).exists())

    def test_api_bloqueia_requisicao_de_outra_empresa_ou_loja_fora_escopo(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        req_b = self.criar_requisicao(15, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, user=self.user_b)
        req_b.status = "APROVADA"
        req_b.save(update_fields=["status"])
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req_b.id]}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Fora Cot", apelido_loja="Loja Fora Cot", cnpj="33111111000614", estado="SP")
        req_fora, _ = self._req_aprovada_com_itens(16, loja=loja_extra)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req_fora.id]}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_api_necessidades_agrupa_mesmo_produto_cadastrado(self):
        client = APIClient()
        client.force_authenticate(self.user)
        req1, _ = self._req_aprovada_com_produto(17, Decimal("2.000"))
        req2, _ = self._req_aprovada_com_produto(18, Decimal("3.000"))
        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        grupo = next(row for row in resp.data if row["produto"] == self.produto.pkproduto)
        self.assertEqual(grupo["numero_requisicoes"], 2)
        self.assertEqual(set(grupo["requisicoes_ids"]), {req1.id, req2.id})
        self.assertEqual(Decimal(str(grupo["quantidade_pendente"])), Decimal("5.000"))

    def test_api_necessidades_nao_agrupa_item_livre_por_descricao(self):
        client = APIClient()
        client.force_authenticate(self.user)
        req1 = self.criar_requisicao(19)
        req2 = self.criar_requisicao(20)
        for req in (req1, req2):
            req.status = "APROVADA"
            req.save(update_fields=["status"])
            RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="LIVRE", descricao="Caneta livre", unidade=self.unidade, categoria_material=self.categoria_material, qtd_solicitada=Decimal("1.000"), qtd_pendente=Decimal("1.000"))
        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        livres = [row for row in resp.data if row["nome"] == "Caneta livre"]
        self.assertEqual(len(livres), 2)

    def test_api_necessidades_respeita_categoria(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self._req_aprovada_com_produto(21, Decimal("2.000"), categoria=self.categoria_material)
        produto_limpeza = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Detergente", unidade=self.unidade)
        self._req_aprovada_com_produto(22, Decimal("4.000"), categoria=self.categoria_limpeza, produto=produto_limpeza)
        resp = client.get("/api/compras/cotacoes/necessidades/", {"categoria": self.categoria_limpeza.id})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([row["produto"] for row in resp.data], [produto_limpeza.Idproduto])

    def test_api_necessidades_respeita_empresa_lojas_e_ignora_atendida(self):
        client = APIClient()
        client.force_authenticate(self.user)
        req_permitida, _ = self._req_aprovada_com_produto(23, Decimal("2.000"))
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Nec Fora", apelido_loja="Loja Nec Fora", cnpj="33111111000703", estado="SP")
        self._req_aprovada_com_produto(24, Decimal("3.000"), loja=loja_extra)
        req_b = self.criar_requisicao(25, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, user=self.user_b)
        req_b.status = "APROVADA"
        req_b.save(update_fields=["status"])
        RequisicaoItem.objects.create(requisicao=req_b, tipo="MATERIAL", origem="LIVRE", descricao="Outra empresa", unidade=self.unidade_b, qtd_solicitada=Decimal("9.000"), qtd_pendente=Decimal("9.000"))
        req_atendida, item_atendido = self._req_aprovada_com_produto(26, Decimal("5.000"))
        item_atendido.qtd_pendente = Decimal("0.000")
        item_atendido.save(update_fields=["qtd_pendente"])
        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        nomes = [row["nome"] for row in resp.data]
        self.assertIn(self.produto.descricao, nomes)
        self.assertNotIn("Outra empresa", nomes)
        grupo = next(row for row in resp.data if row["produto"] == self.produto.pkproduto)
        self.assertEqual(set(grupo["requisicoes_ids"]), {req_permitida.id})

    def test_api_cotacao_enxerga_requisicoes_de_outro_usuario_sem_permissao_requisicoes(self):
        User = get_user_model()
        requisitante = User.objects.create_user("joao-cot-req", "joaocotreq@test.local", "123", empresa=self.empresa, loja=self.loja)
        cotador = User.objects.create_user("maria-cot", "mariacot@test.local", "123", empresa=self.empresa, loja=self.loja)
        perfil_cotacao = PerfilAcesso.objects.create(empresa=self.empresa, nome="Cotação sem Requisição")
        PerfilModuloPermissao.objects.create(perfil=perfil_cotacao, modulo=self.mod_compras, acesso=UserModulePermission.Access.EDIT)
        PerfilProcessPermission.objects.create(perfil=perfil_cotacao, codigo="cotacao.aprovar", permitido=True)
        cotador.perfil_principal = perfil_cotacao
        cotador.save(update_fields=["perfil_principal"])

        req = self.criar_requisicao(230, user=requisitante)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        RequisicaoItem.objects.create(
            requisicao=req,
            tipo="MATERIAL",
            origem="LIVRE",
            descricao="Necessidade de outro requisitante",
            unidade=self.unidade,
            categoria_material=self.categoria_material,
            qtd_solicitada=Decimal("4.000"),
            qtd_pendente=Decimal("4.000"),
            status="APROVADO",
        )

        client = APIClient()
        client.force_authenticate(cotador)
        resp = client.get("/api/compras/cotacoes/requisicoes-disponiveis/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn(req.id, [row["id"] for row in resp.data])

        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        origem_ids = {origem["requisicao"] for row in resp.data for origem in row["origens"]}
        self.assertIn(req.id, origem_ids)

        cotacao = self.criar_cotacao(responsavel=cotador)
        resp = client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-requisicoes/", {"requisicoes": [req.id]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(CotacaoRequisicao.objects.filter(cotacao=cotacao, requisicao=req).exists())

        resp = client.get("/api/compras/requisicoes/", {"visao": "todas"})
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_api_cotacao_requisicoes_respeita_empresa_loja_e_elegibilidade_para_usuario_cotacao(self):
        User = get_user_model()
        cotador = User.objects.create_user("maria-cot-escopo", "mariacotescopo@test.local", "123", empresa=self.empresa, loja=self.loja)
        perfil_cotacao = PerfilAcesso.objects.create(empresa=self.empresa, nome="Cotação Escopo")
        PerfilModuloPermissao.objects.create(perfil=perfil_cotacao, modulo=self.mod_compras, acesso=UserModulePermission.Access.EDIT)
        PerfilProcessPermission.objects.create(perfil=perfil_cotacao, codigo="cotacao.aprovar", permitido=True)
        cotador.perfil_principal = perfil_cotacao
        cotador.save(update_fields=["perfil_principal"])

        req_ok, _ = self._req_aprovada_com_itens(231)
        loja_fora = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Cot Fora Escopo", apelido_loja="Loja Cot Fora Escopo", cnpj="33111111001661", estado="SP")
        req_fora, _ = self._req_aprovada_com_itens(232, loja=loja_fora)
        req_b = self.criar_requisicao(233, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, user=self.user_b)
        req_b.status = "APROVADA"
        req_b.save(update_fields=["status"])
        RequisicaoItem.objects.create(requisicao=req_b, tipo="MATERIAL", origem="LIVRE", descricao="Outra empresa cotação", unidade=self.unidade_b, qtd_solicitada=Decimal("1.000"), qtd_pendente=Decimal("1.000"))
        req_sem_pendente, itens_sem_pendente = self._req_aprovada_com_itens(234)
        for item_sem_pendente in itens_sem_pendente:
            item_sem_pendente.qtd_pendente = Decimal("0.000")
            item_sem_pendente.save(update_fields=["qtd_pendente"])

        client = APIClient()
        client.force_authenticate(cotador)
        resp = client.get("/api/compras/cotacoes/requisicoes-disponiveis/")
        self.assertEqual(resp.status_code, 200, resp.data)
        ids = {row["id"] for row in resp.data}
        self.assertIn(req_ok.id, ids)
        self.assertNotIn(req_fora.id, ids)
        self.assertNotIn(req_b.id, ids)

        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        origem_ids = {origem["requisicao"] for row in resp.data for origem in row["origens"]}
        self.assertIn(req_ok.id, origem_ids)
        self.assertNotIn(req_fora.id, origem_ids)
        self.assertNotIn(req_b.id, origem_ids)
        self.assertNotIn(req_sem_pendente.id, origem_ids)

    def test_api_requisicao_item_sem_estoque_sem_compra_indica_vermelho(self):
        client = APIClient()
        client.force_authenticate(self.user)
        PerfilProcessPermission.objects.create(perfil=self.perfil_compras, codigo="requisicoes.atender", permitido=True)
        req = self.criar_requisicao(30)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"), status="APROVADO")
        resp = client.get(f"/api/compras/requisicao-itens/{item.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["indicador_compra"]["cor"], "VERMELHO")
        self.assertEqual(resp.data["indicador_compra"]["codigo"], "PRECISA_COMPRAR")

    def test_api_requisicao_item_em_cotacao_indica_amarelo_e_preserva_link(self):
        client = APIClient()
        client.force_authenticate(self.user)
        PerfilProcessPermission.objects.create(perfil=self.perfil_compras, codigo="requisicoes.atender", permitido=True)
        req = self.criar_requisicao(31)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"), status="APROVADO")
        cotacao = self.criar_cotacao(status="ABERTA")
        CotacaoRequisicao.objects.create(cotacao=cotacao, requisicao=req)
        CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("5.000"), unidade=self.unidade, origem="REQUISICAO", requisicao_item_origem=item)
        resp = client.get(f"/api/compras/requisicao-itens/{item.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["indicador_compra"]["cor"], "AMARELO")
        self.assertEqual(resp.data["indicador_compra"]["cotacoes"][0]["id"], cotacao.id)

    def test_api_requisicao_item_com_estoque_suficiente_indica_verde_sem_alterar_solicitada(self):
        client = APIClient()
        client.force_authenticate(self.user)
        PerfilProcessPermission.objects.create(perfil=self.perfil_compras, codigo="requisicoes.atender", permitido=True)
        req = self.criar_requisicao(32)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"), status="APROVADO")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, loja=self.loja, produto=self.produto, saldo=Decimal("20.000"))
        resp = client.get(f"/api/compras/requisicao-itens/{item.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["indicador_compra"]["cor"], "VERDE")
        item.refresh_from_db()
        self.assertEqual(item.qtd_solicitada, Decimal("5.000"))

    def test_api_atendimento_parcial_baixa_estoque_e_recalcula_indicador(self):
        client = APIClient()
        client.force_authenticate(self.user)
        PerfilProcessPermission.objects.create(perfil=self.perfil_compras, codigo="requisicoes.atender", permitido=True)
        req = self.criar_requisicao(33)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        item = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"), status="APROVADO")
        estoque = ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, loja=self.loja, produto=self.produto, saldo=Decimal("20.000"))
        resp = client.post(f"/api/compras/requisicao-itens/{item.id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        item.refresh_from_db()
        estoque.refresh_from_db()
        self.assertEqual(item.qtd_atendida, Decimal("2.000"))
        self.assertEqual(item.qtd_pendente, Decimal("3.000"))
        self.assertEqual(estoque.saldo, Decimal("18.000"))
        resp = client.get(f"/api/compras/requisicao-itens/{item.id}/")
        self.assertEqual(resp.data["indicador_compra"]["cor"], "VERDE")

    def test_api_necessidades_nao_mistura_empresa_loja_e_ignora_item_com_estoque(self):
        client = APIClient()
        client.force_authenticate(self.user)
        req = self.criar_requisicao(34)
        req.status = "APROVADA"
        req.save(update_fields=["status"])
        RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=self.produto, unidade=self.unidade, qtd_solicitada=Decimal("5.000"), qtd_pendente=Decimal("5.000"), status="APROVADO")
        produto_com_estoque = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Material disponível", unidade=self.unidade)
        item_verde = RequisicaoItem.objects.create(requisicao=req, tipo="MATERIAL", origem="PRODUTO", produto=produto_com_estoque, unidade=self.unidade, qtd_solicitada=Decimal("2.000"), qtd_pendente=Decimal("2.000"), status="APROVADO")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, loja=self.loja, produto=produto_com_estoque, saldo=Decimal("2.000"))
        req_b = self.criar_requisicao(34, empresa=self.empresa_b, loja=self.loja_b, setor=self.setor_b, user=self.user_b)
        req_b.status = "APROVADA"
        req_b.save(update_fields=["status"])
        produto_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Material B", unidade=self.unidade_b)
        RequisicaoItem.objects.create(requisicao=req_b, tipo="MATERIAL", origem="PRODUTO", produto=produto_b, unidade=self.unidade_b, qtd_solicitada=Decimal("8.000"), qtd_pendente=Decimal("8.000"), status="APROVADO")
        resp = client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        produtos = {row["produto"] for row in resp.data}
        self.assertIn(self.produto.pkproduto, produtos)
        self.assertNotIn(produto_com_estoque.Idproduto, produtos)
        self.assertNotIn(produto_b.Idproduto, produtos)
        self.assertEqual(item_verde.qtd_solicitada, Decimal("2.000"))

    def _pedido_compra_produto(self, emissao, qtd, recebido, preco, loja=None, fornecedor=None, status="AP"):
        fornecedor = fornecedor or Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=f"44{PedidoCompra.objects.count():012d}",
            cnpj=f"44{PedidoCompra.objects.count():012d}",
            nome_fornecedor=f"Fornecedor {PedidoCompra.objects.count()}",
            categoria="USO_CONSUMO",
        )
        pedido = PedidoCompra.objects.create(empresa=self.empresa, tipo="2", loja=loja or self.loja, fornecedor=fornecedor, emissao=emissao, status=status)
        item = PedidoCompraItem.objects.create(pedido=pedido, produto=self.produto, qtd=qtd, preco_unit=preco)
        PedidoCompraEntrega.objects.create(item=item, qtd_prevista=qtd, qtd_recebida=recebido, data_recebida=emissao if recebido else None, status="RECB" if recebido >= qtd else "PARC")
        return item

    def test_api_apoio_decisao_calcula_necessidade_estoque_pendente_e_historico(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("8.000"), unidade=self.unidade, origem="AVULSO")
        self._req_aprovada_com_produto(27, Decimal("6.000"))
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=self.produto, loja=self.loja, saldo=Decimal("7.000"))
        forn_a = Fornecedor.objects.create(empresa=self.empresa, tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA, documento="55111111000191", cnpj="55111111000191", nome_fornecedor="Fornecedor A", categoria="USO_CONSUMO")
        self._pedido_compra_produto(timezone.localdate() - timedelta(days=30), Decimal("10.000"), Decimal("4.000"), Decimal("2.80"), fornecedor=forn_a)
        self._pedido_compra_produto(timezone.localdate() - timedelta(days=60), Decimal("30.000"), Decimal("30.000"), Decimal("2.60"), fornecedor=forn_a, status="AT")
        self._pedido_compra_produto(timezone.localdate() - timedelta(days=90), Decimal("50.000"), Decimal("50.000"), Decimal("2.40"), fornecedor=forn_a, status="AT")
        self._pedido_compra_produto(timezone.localdate() - timedelta(days=120), Decimal("70.000"), Decimal("70.000"), Decimal("2.20"), fornecedor=forn_a, status="AT")
        resp = client.get(f"/api/compras/cotacao-itens/{item.id}/apoio-decisao/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(str(resp.data["necessidade_aberta"])), Decimal("6.000"))
        self.assertEqual(Decimal(str(resp.data["estoque_atual"])), Decimal("7.000"))
        self.assertEqual(Decimal(str(resp.data["pedidos_pendentes"])), Decimal("6.000"))
        self.assertEqual(len(resp.data["ultimas_compras"]), 3)
        self.assertEqual(Decimal(str(resp.data["ultimo_preco"])), Decimal("2.80"))
        self.assertEqual(Decimal(str(resp.data["preco_medio"])).quantize(Decimal("0.01")), Decimal("2.60"))
        self.assertEqual(Decimal(str(resp.data["media_quantidades_ultimas_compras"])).quantize(Decimal("0.01")), Decimal("28.00"))
        self.assertEqual(Decimal(str(resp.data["quantidade_cotar"])), Decimal("8.000"))

    def test_api_apoio_decisao_item_sem_historico_e_avulso(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item_produto = CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        resp = client.get(f"/api/compras/cotacao-itens/{item_produto.id}/apoio-decisao/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["ultimas_compras"], [])
        self.assertIsNone(resp.data["ultimo_preco"])
        item_avulso = CotacaoItem.objects.create(cotacao=cotacao, descricao="Livre", quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        resp = client.get(f"/api/compras/cotacao-itens/{item_avulso.id}/apoio-decisao/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data["necessidade_aberta"])
        self.assertEqual(resp.data["ultimas_compras"], [])

    def test_api_apoio_decisao_respeita_empresa_e_loja(self):
        client = APIClient()
        client.force_authenticate(self.user)
        cotacao = self.criar_cotacao()
        item = CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("1.000"), unidade=self.unidade, origem="AVULSO")
        self._req_aprovada_com_produto(28, Decimal("2.000"))
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Apoio Fora", apelido_loja="Loja Apoio Fora", cnpj="33111111000894", estado="SP")
        self._req_aprovada_com_produto(29, Decimal("9.000"), loja=loja_extra)
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=self.produto, loja=self.loja, saldo=Decimal("4.000"))
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=self.produto, loja=loja_extra, saldo=Decimal("99.000"))
        self._pedido_compra_produto(timezone.localdate(), Decimal("5.000"), Decimal("1.000"), Decimal("2.00"))
        self._pedido_compra_produto(timezone.localdate(), Decimal("9.000"), Decimal("0.000"), Decimal("2.00"), loja=loja_extra)
        resp = client.get(f"/api/compras/cotacao-itens/{item.id}/apoio-decisao/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(str(resp.data["necessidade_aberta"])), Decimal("2.000"))
        self.assertEqual(Decimal(str(resp.data["estoque_atual"])), Decimal("4.000"))
        self.assertEqual(Decimal(str(resp.data["pedidos_pendentes"])), Decimal("4.000"))


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
        self.config_ean = ConfigEan.objects.create(empresa=self.empresa, company_prefix="2701", ativo=True)
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

    def test_item_revenda_serializer_expoe_descricao_reduzida_referencia_e_sku_unico(self):
        self.prod_revenda.descricao_reduzida = "Revenda Red."
        self.prod_revenda.save(update_fields=["descricao_reduzida"])
        pack_unico = Pack.objects.create(empresa=self.empresa, nome="Pack P", grade=self.grade)
        PackItem.objects.create(pack=pack_unico, tamanho=self.tam_p, qtd=1)
        sku = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=self.cor, idtamanho=self.tam_p)
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido, pack=pack_unico.id))

        resp = self.client.get(f"/api/compras/itens/{item.id}/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["produto_descricao_reduzida"], "Revenda Red.")
        self.assertEqual(resp.data["produto_referencia"], self.prod_revenda.referencia)
        self.assertEqual(resp.data["pack_nome"], "Pack P")
        self.assertEqual(resp.data["sku_ean"], sku.ean13)
        self.assertEqual(resp.data["sku_count"], 1)

    def test_item_revenda_serializer_nao_escolhe_ean_arbitrario_para_pack_com_multiplos_skus(self):
        sku_p = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=self.cor, idtamanho=self.tam_p)
        sku_m = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=self.cor, idtamanho=self.tam_m)
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido))

        resp = self.client.get(f"/api/compras/itens/{item.id}/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["sku_ean"], "")
        self.assertEqual(resp.data["sku_count"], 2)
        self.assertIn(sku_p.ean13, resp.data["sku_tooltip"])
        self.assertIn(sku_m.ean13, resp.data["sku_tooltip"])

    def test_item_revenda_serializer_lista_codigos_reais_filtrados_e_na_ordem_do_pack(self):
        cor_outra = Cor.objects.create(empresa=self.empresa, Descricao="Verde", Codigo="VD", Cor="Verde")
        produto_outro = self.criar_produto("1", "Outra Revenda", self.un_int)
        pack_invertido = Pack.objects.create(empresa=self.empresa, nome="Pack M P", grade=self.grade)
        PackItem.objects.create(pack=pack_invertido, tamanho=self.tam_m, qtd=2)
        PackItem.objects.create(pack=pack_invertido, tamanho=self.tam_p, qtd=1)
        sku_p = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=self.cor, idtamanho=self.tam_p)
        sku_m = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=self.cor, idtamanho=self.tam_m)
        ProdutoDetalhe.objects.filter(pk=sku_p.pk).update(ean13="")
        sku_p.ean13 = ""
        sku_outra_cor = ProdutoDetalhe.objects.create(produto=self.prod_revenda, idcor=cor_outra, idtamanho=self.tam_m)
        sku_outro_produto = ProdutoDetalhe.objects.create(produto=produto_outro, idcor=self.cor, idtamanho=self.tam_m)
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido, pack=pack_invertido.id))

        resp = self.client.get(f"/api/compras/itens/{item.id}/")

        self.assertEqual(resp.status_code, 200, resp.data)
        codigos = resp.data["sku_codigos_barras"]
        self.assertEqual(codigos, [
            {"tamanho": "M", "ean13": sku_m.ean13},
            {"tamanho": "P", "ean13": ""},
        ])
        self.assertNotIn(sku_outra_cor.ean13, [sku["ean13"] for sku in codigos])
        self.assertNotIn(sku_outro_produto.ean13, [sku["ean13"] for sku in codigos])
        self.assertIn("P - Sem EAN", resp.data["sku_tooltip"])

    def test_pack_nunca_utilizado_continua_editavel(self):
        livre = Pack.objects.create(empresa=self.empresa, nome="Pack Livre", grade=self.grade)
        item = PackItem.objects.create(pack=livre, tamanho=self.tam_p, qtd=1)

        resp = self.client.patch(f"/api/produto/pack/{livre.id}/", {"empresa": self.empresa.id, "nome": "Pack Livre 2"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post("/api/produto/pack-item/", {"pack": livre.id, "tamanho": self.tam_m.Idtamanho, "qtd": 2}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.client.patch(f"/api/produto/pack-item/{item.id}/", {"qtd": 3}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.delete(f"/api/produto/pack-item/{item.id}/")
        self.assertEqual(resp.status_code, 204, resp.data if hasattr(resp, "data") else resp.content)

    def test_pack_usado_em_pedido_historico_bloqueia_cabecalho_e_grade(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_revenda(pedido))
        PedidoCompra.objects.filter(pk=pedido.pk).update(status="AP")
        item = self.pack.itens.first()
        tam_g = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="G", Descricao="G")

        for method, url, payload in (
            ("patch", f"/api/produto/pack/{self.pack.id}/", {"empresa": self.empresa.id, "nome": "Alterado"}),
            ("delete", f"/api/produto/pack/{self.pack.id}/", None),
            ("post", "/api/produto/pack-item/", {"pack": self.pack.id, "tamanho": tam_g.Idtamanho, "qtd": 4}),
            ("patch", f"/api/produto/pack-item/{item.id}/", {"qtd": 4}),
            ("delete", f"/api/produto/pack-item/{item.id}/", None),
        ):
            request = getattr(self.client, method)
            resp = request(url, payload, format="json") if payload is not None else request(url)
            self.assertEqual(resp.status_code, 400, resp.data)
            self.assertIn("Pedido de Compra", str(resp.data))

    def test_pack_permanece_bloqueado_em_status_atendido_e_cancelado(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_revenda(pedido))
        for status_pc in ("AT", "CA"):
            PedidoCompra.objects.filter(pk=pedido.pk).update(status=status_pc)
            resp = self.client.patch(f"/api/produto/pack/{self.pack.id}/", {"empresa": self.empresa.id, "ativo": False}, format="json")
            self.assertEqual(resp.status_code, 400, resp.data)

    def test_pack_de_outra_empresa_nao_bloqueia_pack_local(self):
        grade_b = Grade.objects.create(empresa=self.empresa_b, Descricao="Grade B")
        tam_b = Tamanho.objects.create(empresa=self.empresa_b, idgrade=grade_b, Tamanho="P", Descricao="P")
        pack_b = Pack.objects.create(empresa=self.empresa_b, nome="Pack B", grade=grade_b)
        PackItem.objects.create(pack=pack_b, tamanho=tam_b, qtd=1)
        prod_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="1", descricao="Revenda B", unidade=self.un_int, grupo=self.grupo, colecao=self.colecao, grade=grade_b)
        cor_b = Cor.objects.create(empresa=self.empresa_b, Descricao="Azul B", Codigo="AZB", Cor="Azul")
        pedido_b = PedidoCompra.objects.create(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, tipo="1", status="AP")
        PedidoCompraItem.objects.create(pedido=pedido_b, produto=prod_b, cor=cor_b, pack=pack_b, n_packs=1, qtd=1, preco_unit=Decimal("1.00"), total_item=Decimal("1.00"))

        livre = Pack.objects.create(empresa=self.empresa, nome="Pack Empresa A Livre", grade=self.grade)
        resp = self.client.patch(f"/api/produto/pack/{livre.id}/", {"empresa": self.empresa.id, "nome": "Pack Empresa A Editado"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_pack_item_de_pack_bloqueado_nao_pode_ser_transferido_para_pack_livre(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_revenda(pedido))
        PedidoCompra.objects.filter(pk=pedido.pk).update(status="AP")
        pack_livre = Pack.objects.create(empresa=self.empresa, nome="Pack Livre Destino", grade=self.grade)
        item = self.pack.itens.first()

        resp = self.client.patch(f"/api/produto/pack-item/{item.id}/", {"pack": pack_livre.id}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Pedido de Compra", str(resp.data))
        item.refresh_from_db()
        self.assertEqual(item.pack_id, self.pack.id)

    def test_pack_item_de_pack_livre_nao_pode_ser_transferido_para_pack_bloqueado(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_revenda(pedido))
        PedidoCompra.objects.filter(pk=pedido.pk).update(status="AP")
        tam_g = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="G", Descricao="G")
        pack_livre = Pack.objects.create(empresa=self.empresa, nome="Pack Livre Origem", grade=self.grade)
        item_livre = PackItem.objects.create(pack=pack_livre, tamanho=tam_g, qtd=1)

        resp = self.client.patch(f"/api/produto/pack-item/{item_livre.id}/", {"pack": self.pack.id}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Pedido de Compra", str(resp.data))
        item_livre.refresh_from_db()
        self.assertEqual(item_livre.pack_id, pack_livre.id)

    def test_pack_item_pode_ser_transferido_entre_packs_livres(self):
        pack_origem = Pack.objects.create(empresa=self.empresa, nome="Pack Livre Origem", grade=self.grade)
        pack_destino = Pack.objects.create(empresa=self.empresa, nome="Pack Livre Destino", grade=self.grade)
        item = PackItem.objects.create(pack=pack_origem, tamanho=self.tam_p, qtd=1)

        resp = self.client.patch(f"/api/produto/pack-item/{item.id}/", {"pack": pack_destino.id}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        item.refresh_from_db()
        self.assertEqual(item.pack_id, pack_destino.id)


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
        self.outro_mesma_empresa = User.objects.create_user("req-colega", "reqc@test.local", "123", empresa=self.empresa, loja=self.loja, type="AssistentePagar")
        self.outro = User.objects.create_user("req-outro", "reqo@test.local", "123", empresa=self.empresa_b, loja=self.loja_b, type="Gerente")
        self.perfil_solicitante = self._perfil_requisicao("Solicitante", self.empresa, ["requisicoes.fazer"])
        self.perfil_aprovador = self._perfil_requisicao("Aprovador", self.empresa, ["requisicoes.fazer", "requisicoes.aprovar", "requisicoes.atender"])
        self.perfil_outro = self._perfil_requisicao("Outro", self.empresa_b, ["requisicoes.fazer", "requisicoes.aprovar", "requisicoes.atender"])
        PerfilModuloPermissao.objects.filter(perfil__in=[self.perfil_solicitante, self.perfil_aprovador, self.perfil_outro], modulo=self.mod_compras).update(acesso=UserModulePermission.Access.EDIT)
        self.solicitante.perfil_principal = self.perfil_solicitante
        self.outro_mesma_empresa.perfil_principal = self.perfil_solicitante
        self.aprovador.perfil_principal = self.perfil_aprovador
        self.outro.perfil_principal = self.perfil_outro
        for user in (self.solicitante, self.aprovador, self.outro_mesma_empresa, self.outro):
            user.save(update_fields=["perfil_principal"])
        self.unidade = self.un_int
        self.produto = self.prod_uso
        self.categoria = RequisicaoServicoCategoria.objects.create(empresa=self.empresa, nome="Informática")
        self.categoria_material = RequisicaoMaterialCategoria.objects.create(empresa=self.empresa, nome="Informática")
        self.finalidade_uso = RequisicaoFinalidadeAquisicao.objects.create(empresa=self.empresa, nome="Uso e Consumo", comportamento="USO_CONSUMO")
        self.finalidade_almox = RequisicaoFinalidadeAquisicao.objects.create(empresa=self.empresa, nome="Estoque/Almoxarifado", comportamento="ALMOXARIFADO")
        self.finalidade_imob = RequisicaoFinalidadeAquisicao.objects.create(empresa=self.empresa, nome="Imobilizado", comportamento="IMOBILIZADO")
        self.finalidade_outro = RequisicaoFinalidadeAquisicao.objects.create(empresa=self.empresa, nome="Outro", comportamento="OUTRO")
        self.setor = RequisicaoSetor.objects.create(empresa=self.empresa, loja=self.loja, nome="Financeiro")
        self.setor_b = RequisicaoSetor.objects.create(empresa=self.empresa_b, loja=self.loja_b, nome="Financeiro B")
        self.almoxarifado = RequisicaoSetor.objects.create(empresa=self.empresa, loja=self.loja, nome="Almoxarifado Central", central_uso_consumo=True, recebe_requisicoes=True)
        self.manutencao = RequisicaoSetor.objects.create(empresa=self.empresa, loja=self.loja, nome="Manutenção", central_manutencao=True, recebe_requisicoes=True)
        self.ti = RequisicaoSetor.objects.create(empresa=self.empresa, loja=self.loja, nome="TI", central_ti=True, recebe_requisicoes=True, responsavel_compras=True)
        self.compras = RequisicaoSetor.objects.create(empresa=self.empresa, nome="Compras", responsavel_compras=True)
        self.almoxarifado_b = RequisicaoSetor.objects.create(empresa=self.empresa_b, loja=self.loja_b, nome="Almoxarifado B", central_uso_consumo=True, recebe_requisicoes=True)
        self.compras_b = RequisicaoSetor.objects.create(empresa=self.empresa_b, nome="Compras B", responsavel_compras=True)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa, tipo_requisicao="USO_CONSUMO", setor_atendimento=self.almoxarifado, setor_aquisicao=self.compras)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa, tipo_requisicao="MANUTENCAO", setor_atendimento=self.manutencao, setor_aquisicao=self.compras)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa, tipo_requisicao="TI", setor_atendimento=self.ti, setor_aquisicao=self.ti)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa_b, tipo_requisicao="USO_CONSUMO", setor_atendimento=self.almoxarifado_b, setor_aquisicao=self.compras_b)
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=self.produto, loja=self.loja, saldo=Decimal("10.000"))
        self.client.force_authenticate(self.solicitante)

    def _perfil_requisicao(self, nome, empresa, codigos):
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome=nome)
        PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.mod_compras, acesso=UserModulePermission.Access.NONE)
        for codigo in codigos:
            PerfilProcessPermission.objects.create(perfil=perfil, codigo=codigo, permitido=True)
        return perfil

    def _atribuir_perfil_requisicao(self, user, nome, codigos):
        perfil = self._perfil_requisicao(nome, user.empresa, codigos)
        user.perfil_principal = perfil
        user.save(update_fields=["perfil_principal"])
        return perfil

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
            "produto": self.produto.pk,
            "unidade": self.unidade.pk,
            "finalidade_aquisicao": self.finalidade_uso.id,
            "qtd_solicitada": qtd,
            "observacoes": "Papel",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def item_servico(self, req, titulo="Manutenção"):
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "SERVICO",
            "origem": "SERVICO",
            "titulo_servico": titulo,
            "descricao_servico": titulo,
            "categoria_servico": self.categoria.id,
            "tipo_servico": "CORRETIVA",
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

    def test_usuario_ve_suas_proprias_requisicoes_na_visao_minhas(self):
        req = self.criar_requisicao()
        Requisicao.objects.create(numero=2, empresa=self.empresa, loja=self.loja, setor=self.setor, requisitante=self.outro_mesma_empresa, criado_por=self.outro_mesma_empresa, justificativa="Colega")
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual([r["id"] for r in rows], [req.id])

    def test_regressao_admin_todas_enxerga_requisicao_antiga_sem_loja_vinculada_ao_usuario(self):
        User = get_user_model()
        req = self.criar_requisicao()
        admin = User.objects.create_superuser("req-admin", "reqadmin@test.local", "123")
        self.client.force_authenticate(admin)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "todas"})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])

        self.client.force_authenticate(self.solicitante)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])

        self.client.force_authenticate(self.outro_mesma_empresa)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(req.id, [r["id"] for r in rows])
        self.client.force_authenticate(self.solicitante)

    def test_usuario_sem_compras_com_requisicoes_acessa_e_cria(self):
        User = get_user_model()
        requisitante = User.objects.create_user("req-sem-compras", "reqsc@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self._atribuir_perfil_requisicao(requisitante, "Req sem compras", ["requisicoes.fazer"])
        self.client.force_authenticate(requisitante)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": self.loja.id,
            "setor": self.setor.id,
            "prioridade": "NORMAL",
            "justificativa": "Sem compras",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        req_id = resp.data["id"]
        self.item_produto(Requisicao.objects.get(pk=req_id))
        resp = self.client.post(f"/api/compras/requisicoes/{req_id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_lojas_permitidas_usuario_com_uma_loja_retorna_uma(self):
        resp = self.client.get("/api/compras/requisicoes/lojas-permitidas/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([row["id"] for row in resp.data], [self.loja.id])

    def test_lojas_permitidas_usuario_com_duas_lojas_retorna_duas_sem_vazar_empresa(self):
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Extra", apelido_loja="Loja Extra", cnpj="11111111000282", estado="SP")
        self.solicitante.lojas.add(loja_extra, self.loja_b)
        resp = self.client.get("/api/compras/requisicoes/lojas-permitidas/")
        self.assertEqual(resp.status_code, 200, resp.data)
        ids = {row["id"] for row in resp.data}
        self.assertEqual(ids, {self.loja.id, loja_extra.id})
        self.assertNotIn(self.loja_b.id, ids)

    def test_lojas_permitidas_admin_sem_lojas_explicitamente_marcadas_retorna_todas_da_empresa(self):
        User = get_user_model()
        loja_extra = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Admin Extra", apelido_loja="Loja Admin Extra", cnpj="11111111000363", estado="SP")
        admin = User.objects.create_user("admin-lojas-req", "adminlojas@test.local", "123", empresa=self.empresa, loja=self.loja, type="Admin")
        admin.lojas.clear()
        self.client.force_authenticate(admin)
        resp = self.client.get("/api/compras/requisicoes/lojas-permitidas/")
        self.assertEqual(resp.status_code, 200, resp.data)
        ids = {row["id"] for row in resp.data}
        self.assertEqual(ids, {self.loja.id, loja_extra.id})
        self.assertNotIn(self.loja_b.id, ids)
        self.client.force_authenticate(self.solicitante)

    def test_joao_somente_requisitar_cria_envia_e_nao_ve_todas(self):
        User = get_user_model()
        joao = User.objects.create_user("joao-req", "joao@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self._atribuir_perfil_requisicao(joao, "Joao Req", ["requisicoes.fazer"])
        req_alheia = Requisicao.objects.create(numero=2, empresa=self.empresa, loja=self.loja, setor=self.setor, requisitante=self.outro_mesma_empresa, criado_por=self.outro_mesma_empresa, justificativa="Colega")
        self.client.force_authenticate(joao)
        req = self.criar_requisicao(justificativa="Joao requisita")
        self.item_produto(req)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])
        self.assertNotIn(req_alheia.id, [r["id"] for r in rows])
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "todas"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(req.id, [r["id"] for r in rows])
        self.assertNotIn(req_alheia.id, [r["id"] for r in rows])
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "para_analisar"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(rows, [])
        self.client.force_authenticate(self.solicitante)

    def test_paula_com_requisitar_e_analisar_lista_sem_erro(self):
        User = get_user_model()
        paula = User.objects.create_user("paula-req", "paula@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self._atribuir_perfil_requisicao(paula, "Paula Req", ["requisicoes.fazer", "requisicoes.aprovar"])
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(paula)
        for visao in ("minhas", "para_analisar"):
            resp = self.client.get("/api/compras/requisicoes/", {"visao": visao})
            self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_visualizar_todas_nao_existe_para_usuario_comum(self):
        User = get_user_model()
        auditor = User.objects.create_user("auditor-req", "auditor@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self._atribuir_perfil_requisicao(auditor, "Auditor Req", ["requisicoes.fazer"])
        req = self.criar_requisicao()
        self.client.force_authenticate(auditor)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "todas"})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(req.id, [r["id"] for r in rows])
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Auditor editou"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_usuario_somente_atender_ve_fila_de_atendimento(self):
        User = get_user_model()
        atendente = User.objects.create_user("atendente-req", "atendente@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self._atribuir_perfil_requisicao(atendente, "Atendente Req", ["requisicoes.atender"])
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.client.force_authenticate(atendente)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "para_atender"})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "todas"})
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])
        self.client.force_authenticate(self.solicitante)

    def test_gerente_sem_permissao_especifica_nao_ganha_requisicoes_por_tipo(self):
        User = get_user_model()
        gerente = User.objects.create_user("gerente-sem-req", "gerentesemreq@test.local", "123", empresa=self.empresa, loja=self.loja, type="Gerente")
        self.client.force_authenticate(gerente)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        self.assertEqual(resp.status_code, 403, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_usuario_sem_requisicoes_nao_acessa_endpoints(self):
        User = get_user_model()
        sem_acesso = User.objects.create_user("req-sem-acesso", "reqsa@test.local", "123", empresa=self.empresa, loja=self.loja, type="Regular")
        self.client.force_authenticate(sem_acesso)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "minhas"})
        self.assertEqual(resp.status_code, 403, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_usuario_comum_nao_altera_ou_envia_requisicao_de_outro_usuario(self):
        req = Requisicao.objects.create(numero=1, empresa=self.empresa, loja=self.loja, setor=self.setor, requisitante=self.outro_mesma_empresa, criado_por=self.outro_mesma_empresa, justificativa="Colega")
        self.client.force_authenticate(self.outro_mesma_empresa)
        self.item_produto(req)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Invadir"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 404, resp.data)

    def test_requisitante_nao_analisa_e_aprovador_analisa_por_permissao(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.solicitante)

    def test_resolve_setor_responsavel_por_tipo_de_requisicao(self):
        casos = [
            ("USO_CONSUMO", self.almoxarifado.id),
            ("MANUTENCAO", self.manutencao.id),
            ("TI", self.ti.id),
        ]
        for tipo, setor_id in casos:
            req = self.criar_requisicao(tipo_requisicao=tipo)
            self.assertEqual(req.tipo_requisicao, tipo)
            self.assertEqual(req.setor_responsavel_id, setor_id)

    def test_sem_matriz_configurada_bloqueia_criacao_com_erro_claro(self):
        RequisicaoMatrizResponsabilidade.objects.filter(empresa=self.empresa, tipo_requisicao="MANUTENCAO").delete()
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": self.loja.id,
            "setor": self.setor.id,
            "tipo_requisicao": "MANUTENCAO",
            "prioridade": "NORMAL",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Não existe Central de Atendimento configurada", str(resp.data))

    def test_matriz_rejeita_setor_de_outra_empresa(self):
        resp = self.client.post("/api/compras/requisicao-matriz-responsabilidade/", {
            "tipo_requisicao": "TI",
            "setor_atendimento": self.setor_b.id,
            "setor_aquisicao": self.compras.id,
            "ativo": True,
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("outra empresa", str(resp.data))

    def test_matriz_multiempresa_nao_interfere_na_resolucao(self):
        self.client.force_authenticate(self.outro)
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": self.loja_b.id,
            "setor": self.setor_b.id,
            "tipo_requisicao": "USO_CONSUMO",
            "prioridade": "NORMAL",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        req = Requisicao.objects.get(pk=resp.data["id"])
        self.assertEqual(req.setor_responsavel_id, self.almoxarifado_b.id)
        self.client.force_authenticate(self.solicitante)

    def test_requisicao_antiga_continua_legivel_sem_setor_responsavel(self):
        req = Requisicao.objects.create(numero=99, empresa=self.empresa, loja=self.loja, setor=self.setor, requisitante=self.solicitante, criado_por=self.solicitante, justificativa="Antiga", setor_responsavel=None)
        resp = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["tipo_requisicao"], "USO_CONSUMO")
        self.assertIsNone(resp.data["setor_responsavel"])

    def test_requisicao_manutencao_gera_ordem_servico_ao_aprovar(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO", justificativa="Ar condicionado sem gelar")
        self.item_servico(req, titulo="Ar condicionado sem gelar")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        self.assertEqual(os.empresa_id, self.empresa.id)
        self.assertEqual(os.loja_id, self.loja.id)
        self.assertEqual(os.setor_solicitante_id, self.setor.id)
        self.assertEqual(os.setor_responsavel_id, self.manutencao.id)
        self.assertEqual(os.tipo, "MANUTENCAO")
        self.assertEqual(os.origem, "REQUISICAO")
        self.assertIn("Ar condicionado", os.descricao)
        req.refresh_from_db()
        self.assertEqual(req.status, "EM_ATENDIMENTO")

    def test_requisicao_ti_gera_ordem_servico_ao_aprovar(self):
        req = self.criar_requisicao(tipo_requisicao="TI", justificativa="Computador sem rede")
        self.item_servico(req, titulo="Computador sem rede")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        self.assertEqual(os.setor_responsavel_id, self.ti.id)
        self.assertEqual(os.tipo, "TI")

    def test_requisicao_uso_consumo_nao_gera_ordem_servico(self):
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())

    def test_ordem_servico_nao_duplica_para_mesma_requisicao(self):
        req = self.criar_requisicao(tipo_requisicao="TI")
        self.item_servico(req)
        self.aprovar(req)
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Atualiza"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(OrdemServico.objects.filter(requisicao=req).count(), 1)

    def test_item_servico_em_rascunho_ti_nao_cria_os_nem_muda_status(self):
        req = self.criar_requisicao(tipo_requisicao="TI")
        item_id = self.item_servico(req, titulo="Instalar impressora")
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())

        resp = self.client.patch(f"/api/compras/requisicao-itens/{item_id}/", {
            "titulo_servico": "Instalar impressora fiscal",
            "descricao_servico": "Instalar impressora fiscal",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())

        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "AGUARDANDO_APROVACAO")
        self.assertFalse(OrdemServico.objects.filter(requisicao=req).exists())

        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "EM_ATENDIMENTO")
        self.assertEqual(OrdemServico.objects.filter(requisicao=req).count(), 1)

    def test_retrieve_rascunho_com_os_antiga_nao_sincroniza_para_atendimento(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req, titulo="OS criada cedo")
        OrdemServico.objects.create(
            requisicao=req,
            empresa=req.empresa,
            loja=req.loja,
            setor_solicitante=req.setor,
            setor_responsavel=req.setor_responsavel,
            tipo=req.tipo_requisicao,
            origem="REQUISICAO",
            descricao=req.justificativa,
        )

        resp = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")
        self.assertEqual(OrdemServico.objects.filter(requisicao=req).count(), 1)

    def test_ordem_servico_status_e_conclusao(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {
            "status": "EM_ATENDIMENTO",
            "diagnostico": "Falha no compressor",
            "solucao": "Troca programada",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "EM_ATENDIMENTO")
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"status": "CONCLUIDA"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNotNone(resp.data["data_conclusao"])
        self.client.force_authenticate(self.solicitante)

    def test_os_em_atendimento_com_material_disponivel_mantem_requisicao_em_atendimento(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("1.000"))
        os.status = "EM_ATENDIMENTO"
        os.save(update_fields=["status", "atualizado_em"])
        Requisicao.objects.filter(pk=req.pk).update(status="EM_PROCESSO_COMPRA")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "EM_ATENDIMENTO")

    def test_os_sem_material_conclui_requisicao_e_registra_historico(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        item_id = self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        req.refresh_from_db()
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"status": "CONCLUIDA"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        item = RequisicaoItem.objects.get(pk=item_id)
        self.assertEqual(req.status, "CONCLUIDA")
        self.assertEqual(item.status, "SERVICO_CONCLUIDO")
        self.assertTrue(RequisicaoHistorico.objects.filter(requisicao=req, observacao=f"Atendida pela OS nº {os.id}.").exists())

    def test_consulta_corrige_item_aprovado_de_requisicao_com_os_concluida(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        item_id = self.item_produto(req, qtd="1.000")
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        OrdemServico.objects.filter(pk=os.pk).update(status="CONCLUIDA")
        Requisicao.objects.filter(pk=req.pk).update(status="CONCLUIDA")
        RequisicaoItem.objects.filter(pk=item_id).update(status="APROVADO")
        observacao = f"Atendida pela OS nº {os.id}."
        RequisicaoHistorico.objects.create(requisicao=req, acao="STATUS", status_anterior="EM_ATENDIMENTO", status_novo="CONCLUIDA", observacao=observacao)
        historico_antes = RequisicaoHistorico.objects.filter(requisicao=req, observacao=observacao).count()

        self.client.force_authenticate(self.aprovador)
        resp = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        item = RequisicaoItem.objects.get(pk=item_id)
        req.refresh_from_db()
        self.assertEqual(req.status, "CONCLUIDA")
        self.assertEqual(item.status, "SERVICO_CONCLUIDO")
        self.assertEqual(RequisicaoHistorico.objects.filter(requisicao=req, observacao=observacao).count(), historico_antes)

    def test_ordem_servico_concluida_nao_permite_alteracoes_operacionais(self):
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        OrdemServico.objects.filter(pk=os.pk).update(status="CONCLUIDA")

        self.client.force_authenticate(self.aprovador)
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"status": "EM_ATENDIMENTO"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(str(resp.data["detail"]), "Ordem de Serviço concluída não pode mais ser alterada.")
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"diagnostico": "Alterar"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        detalhe = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)

    def test_ordem_servico_concluida_bloqueia_material(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("5.000")})
        req = self.criar_requisicao(tipo_requisicao="TI")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        material = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("1.000"))
        OrdemServico.objects.filter(pk=os.pk).update(status="CONCLUIDA")

        self.client.force_authenticate(self.aprovador)
        resp = self.client.post("/api/compras/ordens-servico-materiais/", {"ordem_servico": os.id, "produto": self.produto.pk, "qtd_necessaria": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.patch(f"/api/compras/ordens-servico-materiais/{material.id}/", {"qtd_necessaria": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/ordens-servico-materiais/{material.id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.delete(f"/api/compras/ordens-servico-materiais/{material.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_os_com_material_pendente_bloqueia_conclusao(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        self.client.force_authenticate(self.aprovador)
        material = self.client.post("/api/compras/ordens-servico-materiais/", {
            "ordem_servico": os.id,
            "produto": self.produto.pk,
            "qtd_necessaria": "1.000",
        }, format="json")
        self.assertEqual(material.status_code, 201, material.data)
        resp = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"status": "CONCLUIDA"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(str(resp.data["detail"]), "Não é possível concluir a OS enquanto houver material pendente.")
        os.refresh_from_db()
        req.refresh_from_db()
        self.assertNotEqual(os.status, "CONCLUIDA")
        self.assertEqual(req.status, "EM_ATENDIMENTO")

    def test_os_aguardando_material_nao_coloca_requisicao_em_compra_e_conclui_apos_atendimento(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("2.000")})
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        self.client.force_authenticate(self.aprovador)
        material = self.client.post("/api/compras/ordens-servico-materiais/", {
            "ordem_servico": os.id,
            "produto": self.produto.pk,
            "qtd_necessaria": "2.000",
        }, format="json")
        self.assertEqual(material.status_code, 201, material.data)
        OrdemServico.objects.filter(pk=os.pk).update(status="AGUARDANDO_MATERIAL")
        resp = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        atender = self.client.post(f"/api/compras/ordens-servico-materiais/{material.data['id']}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(atender.status_code, 200, atender.data)
        os.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(os.status, "EM_ATENDIMENTO")
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        concluir = self.client.patch(f"/api/compras/ordens-servico/{os.id}/", {"status": "CONCLUIDA"}, format="json")
        self.assertEqual(concluir.status_code, 200, concluir.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "CONCLUIDA")

    def test_ordem_servico_multiempresa_preservado(self):
        self.client.force_authenticate(self.outro)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa_b, tipo_requisicao="TI", setor_atendimento=self.setor_b, setor_aquisicao=self.setor_b)
        req_b = self.criar_requisicao(loja=self.loja_b.id, setor=self.setor_b.id, tipo_requisicao="TI")
        os_b = OrdemServico.objects.get(requisicao=req_b)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.get(f"/api/compras/ordens-servico/{os_b.id}/")
        self.assertEqual(resp.status_code, 404, resp.data)

    def test_ordem_servico_material_atende_pelo_almoxarifado_central(self):
        loja_filial = Loja.objects.create(empresa=self.empresa, nome_loja="Filial OS", apelido_loja="FOS", cnpj="55999999000190")
        self.solicitante.lojas.add(loja_filial)
        self.aprovador.lojas.add(loja_filial)
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("10.000")})
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=loja_filial, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(loja=loja_filial.id, tipo_requisicao="MANUTENCAO", justificativa="Troca de lâmpadas")
        os = OrdemServico.objects.get(requisicao=req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post("/api/compras/ordens-servico-materiais/", {
            "ordem_servico": os.id,
            "produto": self.produto.pk,
            "qtd_necessaria": "4.000",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "DISPONIVEL")
        material_id = resp.data["id"]
        resp = self.client.post(f"/api/compras/ordens-servico-materiais/{material_id}/atender/", {"quantidade": "4.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "ATENDIDA")
        os.refresh_from_db()
        self.assertEqual(os.status, "EM_ATENDIMENTO")
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=self.loja).saldo, Decimal("6.000"))
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=loja_filial).saldo, Decimal("0.000"))
        mov = ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"OS {os.id}").latest("id")
        self.assertEqual(mov.loja_id, self.loja.id)
        self.assertIn(f"MATERIAL:{material_id}", mov.origem)

    def test_ordem_servico_material_permite_atendimento_parcial_e_limita_pendente(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("2.000")})
        req = self.criar_requisicao(tipo_requisicao="TI")
        os = OrdemServico.objects.get(requisicao=req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post("/api/compras/ordens-servico-materiais/", {
            "ordem_servico": os.id,
            "produto": self.produto.pk,
            "qtd_necessaria": "4.000",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "PENDENTE")
        material_id = resp.data["id"]
        resp = self.client.post(f"/api/compras/ordens-servico-materiais/{material_id}/atender/", {"quantidade": "5.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/ordens-servico-materiais/{material_id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(resp.data["qtd_atendida"]), Decimal("2.000"))
        self.assertEqual(Decimal(resp.data["qtd_pendente"]), Decimal("2.000"))
        self.assertEqual(resp.data["status"], "PENDENTE")
        os.refresh_from_db()
        self.assertEqual(os.status, "AGUARDANDO_MATERIAL")

    def test_ordem_servico_material_disponivel_nao_mantem_os_aguardando_material(self):
        outro_produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Material sem estoque OS", unidade=self.unidade)
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("1.000")})
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=outro_produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req)
        self.aprovar(req)
        os = OrdemServico.objects.get(requisicao=req)
        material_disponivel = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("1.000"))
        material_compra = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=outro_produto, qtd_necessaria=Decimal("1.000"))
        OrdemServicoMaterial.objects.filter(pk=material_compra.pk).update(status="EM_COMPRA")

        self.client.force_authenticate(self.aprovador)
        detalhe = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)
        material_disponivel.refresh_from_db()
        material_compra.refresh_from_db()
        os.refresh_from_db()
        self.assertEqual(material_disponivel.status, "DISPONIVEL")
        self.assertEqual(material_compra.status, "EM_COMPRA")
        self.assertEqual(os.status, "AGUARDANDO_MATERIAL")

        material_compra.status = "CANCELADA"
        material_compra.save(update_fields=["status", "atualizado_em"])
        detalhe = self.client.get(f"/api/compras/ordens-servico/{os.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)
        material_disponivel.refresh_from_db()
        material_compra.refresh_from_db()
        os.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(material_disponivel.status, "DISPONIVEL")
        self.assertEqual(material_compra.status, "CANCELADA")
        self.assertEqual(os.status, "EM_ATENDIMENTO")
        self.assertEqual(req.status, "EM_ATENDIMENTO")

    def test_ordem_servico_material_estoque_zero_permanece_pendente_e_sem_afetar_os_vazia(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        os = OrdemServico.objects.get(requisicao=req)
        self.assertEqual(os.status, "ABERTA")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post("/api/compras/ordens-servico-materiais/", {
            "ordem_servico": os.id,
            "produto": self.produto.pk,
            "qtd_necessaria": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "PENDENTE")
        os.refresh_from_db()
        self.assertEqual(os.status, "AGUARDANDO_MATERIAL")

    def test_ordem_servico_material_multiempresa_preservado(self):
        self.client.force_authenticate(self.outro)
        RequisicaoMatrizResponsabilidade.objects.create(empresa=self.empresa_b, tipo_requisicao="TI", setor_atendimento=self.setor_b, setor_aquisicao=self.setor_b)
        req_b = self.criar_requisicao(loja=self.loja_b.id, setor=self.setor_b.id, tipo_requisicao="TI")
        os_b = OrdemServico.objects.get(requisicao=req_b)
        material = OrdemServicoMaterial.objects.create(ordem_servico=os_b, descricao="Cabo HDMI", qtd_necessaria=Decimal("1.000"))
        self.client.force_authenticate(self.solicitante)
        resp = self.client.get(f"/api/compras/ordens-servico-materiais/{material.id}/")
        self.assertEqual(resp.status_code, 404, resp.data)

    def test_necessidades_compra_unifica_requisicao_e_material_os(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="3.000")
        self.aprovar(req)
        req_os = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        os = OrdemServico.objects.get(requisicao=req_os)
        material = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("2.000"))
        self.client.force_authenticate(self.aprovador)
        resp = self.client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        grupo = next(row for row in resp.data if row["produto"] == self.produto.pk)
        self.assertEqual(Decimal(grupo["quantidade_pendente"]), Decimal("5.000"))
        self.assertEqual(Decimal(grupo["quantidade_sem_cobertura"]), Decimal("5.000"))
        self.assertEqual({o["tipo_origem"] for o in grupo["origens"]}, {"REQ", "OS"})
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        resp = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}", f"OS:{material.id}"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(CotacaoItem.objects.filter(cotacao=cotacao, origem="REQUISICAO").count(), 1)
        self.assertEqual(CotacaoItem.objects.filter(cotacao=cotacao, origem="OS").count(), 1)

    def test_necessidades_compra_ignora_item_requisicao_manutencao_com_os(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req_os = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        item_id = self.item_produto(req_os, qtd="3.000")
        self.aprovar(req_os)
        os = OrdemServico.objects.get(requisicao=req_os)
        material = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("2.000"))

        self.client.force_authenticate(self.aprovador)
        resp = self.client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        grupo = next(row for row in resp.data if row["produto"] == self.produto.pk)
        self.assertEqual(Decimal(grupo["quantidade_pendente"]), Decimal("2.000"))
        self.assertEqual(Decimal(grupo["quantidade_sem_cobertura"]), Decimal("2.000"))
        self.assertEqual([o["tipo_origem"] for o in grupo["origens"]], ["OS"])
        self.assertFalse(any(o["tipo_origem"] == "REQ" and o["origem_id"] == item_id for row in resp.data for o in row["origens"]))

        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        resp = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}"]}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"OS:{material.id}"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(CotacaoItem.objects.filter(cotacao=cotacao, origem="REQUISICAO").count(), 0)
        self.assertEqual(CotacaoItem.objects.filter(cotacao=cotacao, origem="OS").count(), 1)

    def test_necessidade_liquida_desconta_estoque_e_cotacao_existente(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("4.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="14.000")
        self.aprovar(req)
        cotacao_existente = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        CotacaoItem.objects.create(cotacao=cotacao_existente, produto=self.produto, unidade=self.unidade, descricao=self.produto.descricao, quantidade_cotar=Decimal("6.000"), origem="REQUISICAO", requisicao_item_origem_id=item_id)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        grupo = next(row for row in resp.data if row["produto"] == self.produto.pk)
        self.assertEqual(Decimal(grupo["quantidade_pendente"]), Decimal("14.000"))
        self.assertEqual(Decimal(grupo["estoque_central"]), Decimal("4.000"))
        self.assertEqual(Decimal(grupo["quantidade_em_compra"]), Decimal("6.000"))
        self.assertEqual(Decimal(grupo["quantidade_sem_cobertura"]), Decimal("4.000"))

    def test_necessidade_totalmente_coberta_nao_disponibiliza_nova_cotacao(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("4.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="5.000")
        self.aprovar(req)
        cotacao_existente = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        CotacaoItem.objects.create(cotacao=cotacao_existente, produto=self.produto, unidade=self.unidade, descricao=self.produto.descricao, quantidade_cotar=Decimal("1.000"), origem="REQUISICAO", requisicao_item_origem_id=item_id)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(any(row["produto"] == self.produto.pk for row in resp.data))
        resp = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}"]}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_necessidade_compra_multiempresa_preservado(self):
        self.client.force_authenticate(self.outro)
        req_b = self.criar_requisicao(loja=self.loja_b.id, setor=self.setor_b.id, tipo_requisicao="USO_CONSUMO")
        RequisicaoItem.objects.create(requisicao=req_b, tipo="MATERIAL", origem="LIVRE", descricao="Item B", unidade=None, qtd_solicitada=Decimal("2.000"), qtd_pendente=Decimal("2.000"))
        req_b.status = "APROVADA"
        req_b.save(update_fields=["status", "atualizado_em"])
        self.client.force_authenticate(self.solicitante)
        resp = self.client.get("/api/compras/cotacoes/necessidades/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(any(o["documento_id"] == req_b.id for row in resp.data for o in row["origens"]))

    def test_cotacao_pedido_nf_material_interno_recebe_no_almoxarifado(self):
        loja_filial = Loja.objects.create(empresa=self.empresa, nome_loja="Filial Compra Interna", apelido_loja="FCI", cnpj="55888888000190")
        self.solicitante.lojas.add(loja_filial)
        self.aprovador.lojas.add(loja_filial)
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=loja_filial, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(loja=loja_filial.id, tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="4.000")
        self.aprovar(req)
        req_os = self.criar_requisicao(loja=loja_filial.id, tipo_requisicao="MANUTENCAO")
        os = OrdemServico.objects.get(requisicao=req_os)
        material_os = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("2.000"))
        self.client.force_authenticate(self.aprovador)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=loja_filial, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        add = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}", f"OS:{material_os.id}"]}, format="json")
        self.assertEqual(add.status_code, 200, add.data)
        cot_itens = list(CotacaoItem.objects.filter(cotacao=cotacao).order_by("id"))
        fornecedor = self.fornecedor
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor, status_participacao="PROPOSTA_RECEBIDA")
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo=f"PCI{cotacao.id}", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo=f"FPCI{cotacao.id}", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante, forma_pagamento=forma.codigo, prazo_pagamento=prazo, total_proposta=Decimal("120.00"))
        for cot_item in cot_itens:
            CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=cot_item, quantidade_ofertada=Decimal("6.000"), preco_unitario=Decimal("10.00"), total_item=Decimal("60.00"))
        cotacao.proposta_vencedora = proposta
        view = CotacaoViewSet()
        request = APIRequestFactory().post("/")
        request.user = self.aprovador
        cotacao.snapshot_proposta_aprovada = view._snapshot_proposta(proposta)
        cotacao.status = "APROVADA"
        cotacao.save(update_fields=["proposta_vencedora", "snapshot_proposta_aprovada", "status", "atualizado_em"])
        pedido = view._gerar_pedido_da_cotacao(cotacao, request)
        self.assertEqual(pedido.loja_id, self.loja.id)
        self.assertNotEqual(pedido.loja_id, loja_filial.id)
        pedido.status = "AP"
        pedido.save(update_fields=["status"])
        pedido_item = pedido.itens.order_by("id").first()
        nota = NotaFiscalEntrada.objects.create(pedido_compra=pedido, modelo="55", serie="1", numero=f"9{pedido.id}", dt_emissao=timezone.localdate(), dt_entrada=timezone.localdate(), criado_por=self.aprovador)
        for pedido_item in pedido.itens.all():
            NotaFiscalEntradaItem.objects.create(nota=nota, pedido_item=pedido_item, qtd_recebida=Decimal("6.000"), preco_unit_nf=Decimal("10.00"), total_item=Decimal("60.00"))
        self.client.force_authenticate(self.aprovador)
        fechar = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/", {}, format="json")
        self.assertEqual(fechar.status_code, 200, fechar.data)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=self.loja).saldo, Decimal("12.000"))
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=loja_filial).saldo, Decimal("0.000"))
        req_item = RequisicaoItem.objects.get(pk=item_id)
        self.assertEqual(req_item.status, "APROVADO")
        req.refresh_from_db()
        self.assertIn(req.status, {"APROVADA", "EM_ATENDIMENTO"})
        material_os.refresh_from_db()
        self.assertEqual(material_os.status, "DISPONIVEL")
        req_os.refresh_from_db()
        self.assertEqual(req_os.ordem_servico.status, "EM_ATENDIMENTO")
        atender_req = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "4.000"}, format="json")
        self.assertEqual(atender_req.status_code, 200, atender_req.data)
        self.assertEqual(atender_req.data["status"], "ATENDIDO")
        atender_os = self.client.post(f"/api/compras/ordens-servico-materiais/{material_os.id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(atender_os.status_code, 200, atender_os.data)
        self.assertEqual(atender_os.data["status"], "ATENDIDA")
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=self.loja).saldo, Decimal("6.000"))
        req.refresh_from_db()
        req_os.refresh_from_db()
        self.assertEqual(req.status, "CONCLUIDA")
        self.assertEqual(req_os.ordem_servico.status, "EM_ATENDIMENTO")
        self.assertNotEqual(req_os.ordem_servico.status, "CONCLUIDA")

    def test_consulta_sincroniza_material_os_antigo_em_compra_com_estoque_disponivel(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("1.000")})
        req_os = self.criar_requisicao(tipo_requisicao="MANUTENCAO")
        self.item_servico(req_os)
        self.aprovar(req_os)
        os = OrdemServico.objects.get(requisicao=req_os)
        material = OrdemServicoMaterial.objects.create(ordem_servico=os, produto=self.produto, qtd_necessaria=Decimal("1.000"))
        OrdemServicoMaterial.objects.filter(pk=material.pk).update(status="EM_COMPRA", qtd_pendente=Decimal("1.000"))
        OrdemServico.objects.filter(pk=os.pk).update(status="AGUARDANDO_MATERIAL")

        self.client.force_authenticate(self.aprovador)
        detalhe = self.client.get(f"/api/compras/ordens-servico-materiais/{material.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)
        material.refresh_from_db()
        os.refresh_from_db()
        self.assertEqual(material.status, "DISPONIVEL")
        self.assertEqual(os.status, "EM_ATENDIMENTO")

    def test_nf_disponibiliza_requisicao_em_compra_para_atendimento_parcial(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="4.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        aguardar = self.client.post(f"/api/compras/requisicao-itens/{item_id}/aguardar-cotacao/", {}, format="json")
        self.assertEqual(aguardar.status_code, 200, aguardar.data)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        add = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}"]}, format="json")
        self.assertEqual(add.status_code, 200, add.data)
        cot_item = CotacaoItem.objects.get(cotacao=cotacao)
        fornecedor = self.fornecedor
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor, status_participacao="PROPOSTA_RECEBIDA")
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo=f"PRP{cotacao.id}", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo=f"FRP{cotacao.id}", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante, forma_pagamento=forma.codigo, prazo_pagamento=prazo, total_proposta=Decimal("20.00"))
        CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=cot_item, quantidade_ofertada=Decimal("2.000"), preco_unitario=Decimal("10.00"), total_item=Decimal("20.00"))
        cotacao.proposta_vencedora = proposta
        view = CotacaoViewSet()
        request = APIRequestFactory().post("/")
        request.user = self.aprovador
        cotacao.snapshot_proposta_aprovada = view._snapshot_proposta(proposta)
        cotacao.status = "APROVADA"
        cotacao.save(update_fields=["proposta_vencedora", "snapshot_proposta_aprovada", "status", "atualizado_em"])
        pedido = view._gerar_pedido_da_cotacao(cotacao, request)
        pedido.status = "AP"
        pedido.save(update_fields=["status"])
        pedido_item = pedido.itens.get()
        nota = NotaFiscalEntrada.objects.create(pedido_compra=pedido, modelo="55", serie="1", numero=f"8{pedido.id}", dt_emissao=timezone.localdate(), dt_entrada=timezone.localdate(), criado_por=self.aprovador)
        NotaFiscalEntradaItem.objects.create(nota=nota, pedido_item=pedido_item, qtd_recebida=Decimal("2.000"), preco_unit_nf=Decimal("10.00"), total_item=Decimal("20.00"))
        fechar = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/", {}, format="json")
        self.assertEqual(fechar.status_code, 200, fechar.data)
        req_item = RequisicaoItem.objects.get(pk=item_id)
        self.assertEqual(req_item.status, "APROVADO")
        req.refresh_from_db()
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        self.assertTrue(CotacaoItem.objects.filter(cotacao=cotacao, requisicao_item_origem_id=item_id).exists())
        self.assertEqual(CotacaoItem.objects.get(cotacao=cotacao).cotacao.pedido_compra_gerado.id, pedido.id)
        atender = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(atender.status_code, 200, atender.data)
        self.assertEqual(atender.data["status"], "ATENDIDO_PARCIALMENTE")
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=self.loja).saldo, Decimal("0.000"))

    def test_nf_retorna_requisicao_aguardando_cotacao_para_atendimento(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="3.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        aguardar = self.client.post(f"/api/compras/requisicao-itens/{item_id}/aguardar-cotacao/", {}, format="json")
        self.assertEqual(aguardar.status_code, 200, aguardar.data)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO")
        add = self.client.post(f"/api/compras/cotacoes/{cotacao.id}/adicionar-necessidades/", {"necessidades": [f"REQ:{item_id}"]}, format="json")
        self.assertEqual(add.status_code, 200, add.data)
        Requisicao.objects.filter(pk=req.pk).update(status="AGUARDANDO_COTACAO")
        cot_item = CotacaoItem.objects.get(cotacao=cotacao)
        participante = CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=self.fornecedor, status_participacao="PROPOSTA_RECEBIDA")
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo=f"PRR{cotacao.id}", descricao="30 dias", num_parcelas=1, intervalo_dias=30)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("1.000000"))
        forma = FormaPagamento.objects.create(empresa=self.empresa, codigo=f"FRR{cotacao.id}", descricao="Boleto", tipo=FormaPagamento.TIPO_BOLETO, num_parcelas=1, prazo_pagamento=prazo)
        proposta = CotacaoProposta.objects.create(cotacao=cotacao, cotacao_fornecedor=participante, forma_pagamento=forma.codigo, prazo_pagamento=prazo, total_proposta=Decimal("30.00"))
        CotacaoPropostaItem.objects.create(proposta=proposta, cotacao_item=cot_item, quantidade_ofertada=Decimal("3.000"), preco_unitario=Decimal("10.00"), total_item=Decimal("30.00"))
        cotacao.proposta_vencedora = proposta
        view = CotacaoViewSet()
        request = APIRequestFactory().post("/")
        request.user = self.aprovador
        cotacao.snapshot_proposta_aprovada = view._snapshot_proposta(proposta)
        cotacao.status = "APROVADA"
        cotacao.save(update_fields=["proposta_vencedora", "snapshot_proposta_aprovada", "status", "atualizado_em"])
        pedido = view._gerar_pedido_da_cotacao(cotacao, request)
        pedido.status = "AP"
        pedido.save(update_fields=["status"])
        pedido_item = pedido.itens.get()
        nota = NotaFiscalEntrada.objects.create(pedido_compra=pedido, modelo="55", serie="1", numero=f"7{pedido.id}", dt_emissao=timezone.localdate(), dt_entrada=timezone.localdate(), criado_por=self.aprovador)
        NotaFiscalEntradaItem.objects.create(nota=nota, pedido_item=pedido_item, qtd_recebida=Decimal("3.000"), preco_unit_nf=Decimal("10.00"), total_item=Decimal("30.00"))
        fechar = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/", {}, format="json")
        self.assertEqual(fechar.status_code, 200, fechar.data)
        req.refresh_from_db()
        req_item = RequisicaoItem.objects.get(pk=item_id)
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        self.assertEqual(req_item.status, "APROVADO")
        self.assertTrue(CotacaoItem.objects.filter(cotacao=cotacao, requisicao_item_origem=req_item).exists())
        atender = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "3.000"}, format="json")
        self.assertEqual(atender.status_code, 200, atender.data)
        self.assertEqual(atender.data["status"], "ATENDIDO")

    def test_consulta_sincroniza_requisicao_antiga_com_estoque_disponivel(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("3.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, qtd="3.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        aguardar = self.client.post(f"/api/compras/requisicao-itens/{item_id}/aguardar-cotacao/", {}, format="json")
        self.assertEqual(aguardar.status_code, 200, aguardar.data)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=self.loja, responsavel=self.aprovador, tipo_compra="USO_CONSUMO", status="PEDIDO_GERADO")
        cot_item = CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("3.000"), unidade=self.unidade, requisicao_item_origem_id=item_id, origem="REQUISICAO")
        pedido = PedidoCompra.objects.create(empresa=self.empresa, loja=self.loja, fornecedor=self.fornecedor, tipo="2", status="AP", cotacao_origem=cotacao)
        PedidoCompraItem.objects.create(pedido=pedido, produto=self.produto, qtd=Decimal("3.000"), preco_unit=Decimal("10.00"), total_item=Decimal("30.00"), unidade=self.unidade, observacoes=f"REQ_ITEM:{item_id}")
        RequisicaoItem.objects.filter(pk=item_id).update(status="AGUARDANDO_COTACAO")
        Requisicao.objects.filter(pk=req.pk).update(status="AGUARDANDO_COTACAO")
        historico_antes = RequisicaoHistorico.objects.filter(requisicao=req).count()

        self.client.force_authenticate(self.solicitante)
        detalhe = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)
        req.refresh_from_db()
        req_item = RequisicaoItem.objects.get(pk=item_id)
        self.assertEqual(req.status, "EM_ATENDIMENTO")
        self.assertEqual(req_item.status, "APROVADO")
        self.assertEqual(detalhe.data["itens"][0]["indicador_compra"]["codigo"], "DISPONIVEL")
        self.assertTrue(CotacaoItem.objects.filter(pk=cot_item.pk, requisicao_item_origem=req_item).exists())
        self.assertEqual(Cotacao.objects.get(pk=cotacao.pk).pedido_compra_gerado.id, pedido.id)

        segunda_consulta = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        self.assertEqual(segunda_consulta.status_code, 200, segunda_consulta.data)
        self.assertEqual(RequisicaoHistorico.objects.filter(requisicao=req).count(), historico_antes)

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

    def test_categoria_material_pode_ser_criada_editada_e_inativada(self):
        resp = self.client.post("/api/compras/requisicao-material-categorias/", {"nome": "Segurança", "descricao": "EPIs"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        categoria_id = resp.data["id"]

        resp = self.client.patch(f"/api/compras/requisicao-material-categorias/{categoria_id}/", {"descricao": "EPIs e alarmes"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["descricao"], "EPIs e alarmes")

        resp = self.client.post(f"/api/compras/requisicao-material-categorias/{categoria_id}/inativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["ativo"])
        resp = self.client.get("/api/compras/requisicao-material-categorias/", {"ativo": "true"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(categoria_id, [r["id"] for r in rows])

    def test_finalidade_pode_editar_nome_sem_perder_comportamento_e_inativar(self):
        resp = self.client.patch(f"/api/compras/requisicao-finalidades-aquisicao/{self.finalidade_imob.id}/", {"nome": "Ativo Fixo"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["nome"], "Ativo Fixo")
        self.assertEqual(resp.data["comportamento"], "IMOBILIZADO")

        resp = self.client.post(f"/api/compras/requisicao-finalidades-aquisicao/{self.finalidade_imob.id}/inativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.get("/api/compras/requisicao-finalidades-aquisicao/", {"ativo": "true"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(self.finalidade_imob.id, [r["id"] for r in rows])

    def test_categoria_e_finalidade_de_outra_empresa_sao_rejeitadas_e_inativas_nao_entram(self):
        req = self.criar_requisicao()
        categoria_b = RequisicaoMaterialCategoria.objects.create(empresa=self.empresa_b, nome="Material B")
        finalidade_b = RequisicaoFinalidadeAquisicao.objects.create(empresa=self.empresa_b, nome="Outro B", comportamento="OUTRO")

        base = {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Item livre",
            "categoria_material": categoria_b.id,
            "finalidade_aquisicao": self.finalidade_uso.id,
            "unidade": self.unidade.pk,
            "qtd_solicitada": "1.000",
        }
        resp = self.client.post("/api/compras/requisicao-itens/", base, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("categoria_material", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {**base, "categoria_material": self.categoria_material.id, "finalidade_aquisicao": finalidade_b.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade_aquisicao", resp.data)

        self.categoria_material.ativo = False
        self.categoria_material.save(update_fields=["ativo"])
        self.finalidade_uso.ativo = False
        self.finalidade_uso.save(update_fields=["ativo"])
        resp = self.client.post("/api/compras/requisicao-itens/", {**base, "categoria_material": self.categoria_material.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_material_livre_exige_categoria_estruturada_e_compatibilidade_finalidade_legada(self):
        req = self.criar_requisicao()
        base = {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Cadeira",
            "finalidade": "IMOBILIZADO",
            "unidade": self.unidade.pk,
            "qtd_solicitada": "1.000",
        }
        resp = self.client.post("/api/compras/requisicao-itens/", base, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("categoria_material", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {**base, "categoria_material": self.categoria_material.id}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["finalidade"], "IMOBILIZADO")
        self.assertEqual(resp.data["finalidade_aquisicao"], self.finalidade_imob.id)

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
            "categoria_material": self.categoria_material.id,
            "finalidade_aquisicao": self.finalidade_imob.id,
            "unidade": self.unidade.pk,
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
            "finalidade_aquisicao": self.finalidade_almox.id,
            "qtd_solicitada": "1.500",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["unidade"], self.un_dec.Idunidade)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.prod_revenda.Idproduto,
            "finalidade_aquisicao": self.finalidade_uso.id,
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
            "categoria_material": self.categoria_material.id,
            "finalidade_aquisicao": self.finalidade_outro.id,
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
            "categoria_material": self.categoria_material.id,
            "unidade": self.unidade.pk,
            "qtd_solicitada": "1.000",
            "especificacao_tecnica": "Intel i5, 8 GB RAM, SSD 500 GB",
        }
        resp = self.client.post("/api/compras/requisicao-itens/", base, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade_aquisicao", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {**base, "finalidade_aquisicao": self.finalidade_imob.id}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["categoria"], "Informática")
        self.assertEqual(resp.data["finalidade"], "IMOBILIZADO")

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.produto.pk,
            "finalidade_aquisicao": self.finalidade_almox.id,
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
            "produto": self.produto.pk,
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
            "finalidade_aquisicao": self.finalidade_uso.id,
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("finalidade_aquisicao", resp.data)

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
            "finalidade_aquisicao": self.finalidade_uso.id,
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("produto", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "finalidade_aquisicao": self.finalidade_uso.id,
            "unidade": self.unidade.pk,
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("descricao", resp.data)

        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "LIVRE",
            "descricao": "Livre sem unidade",
            "categoria_material": self.categoria_material.id,
            "finalidade_aquisicao": self.finalidade_uso.id,
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

    def test_requisicao_bloqueia_setor_de_outra_loja(self):
        loja_tijuca = Loja.objects.create(empresa=self.empresa, nome_loja="Tijuca", apelido_loja="Tijuca", cnpj="55777777000190")
        self.solicitante.lojas.add(loja_tijuca)
        resp = self.client.post("/api/compras/requisicoes/", {
            "loja": loja_tijuca.id,
            "setor": self.setor.id,
            "data_necessaria": timezone.localdate().isoformat(),
            "prioridade": "NORMAL",
            "justificativa": "Teste loja setor",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("setor", resp.data)

    def test_requisicao_enviar_revalida_setor_da_mesma_loja(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        loja_tijuca = Loja.objects.create(empresa=self.empresa, nome_loja="Tijuca 2", apelido_loja="Tijuca 2", cnpj="55777777000270")
        self.solicitante.lojas.add(loja_tijuca)
        Requisicao.objects.filter(pk=req.pk).update(loja=loja_tijuca)

        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("setor", resp.data)
        req.refresh_from_db()
        self.assertEqual(req.status, "RASCUNHO")

    def test_enviada_nao_pode_ser_editada_pelo_requisitante(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Alterar"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_requisicao_concluida_permanece_imutavel_e_consultavel(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("5.000")})
        req = self.criar_requisicao()
        item_id = self.item_produto(req, qtd="1.000")
        Requisicao.objects.filter(pk=req.pk).update(status="CONCLUIDA")
        RequisicaoItem.objects.filter(pk=item_id).update(status="ATENDIDO")

        detalhe = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        self.assertEqual(detalhe.status_code, 200, detalhe.data)
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Alterar"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post("/api/compras/requisicao-itens/", {
            "requisicao": req.id,
            "tipo": "MATERIAL",
            "origem": "PRODUTO",
            "produto": self.produto.pk,
            "unidade": self.unidade.pk,
            "finalidade_aquisicao": self.finalidade_uso.id,
            "qtd_solicitada": "1.000",
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.patch(f"/api/compras/requisicao-itens/{item_id}/", {"qtd_solicitada": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.delete(f"/api/compras/requisicao-itens/{item_id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/aguardar-cotacao/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_devolvida_para_correcao_permite_edicao_e_reenvio_do_requisitante(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/devolver/", {"motivo": "Ajustar motivo"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "DEVOLVIDA_CORRECAO")
        self.client.force_authenticate(self.solicitante)
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Ajustado"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
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
            "categoria_material": self.categoria_material.id,
            "finalidade_aquisicao": self.finalidade_uso.id,
            "unidade": self.unidade.pk,
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
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{livre.data['id']}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{servico.data['id']}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_atendimento_integral_parcial_e_bloqueio_acima_saldo(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req, "5.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "2.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(resp.data["qtd_pendente"]), Decimal("3.000"))
        self.assertEqual(resp.data["status"], "ATENDIDO_PARCIALMENTE")
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "4.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "3.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "ATENDIDO")
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(origem__startswith="REQUISICAO").count(), 2)

    def test_uso_consumo_consulta_e_baixa_estoque_do_almoxarifado_central(self):
        loja_filial = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Filial Req", apelido_loja="Filial Req", cnpj="11111111000444", estado="SP")
        self.solicitante.lojas.add(loja_filial)
        self.aprovador.lojas.add(loja_filial)
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("20.000")})
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=loja_filial, defaults={"saldo": Decimal("0.000")})
        req = self.criar_requisicao(loja=loja_filial.id, tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, "5.000")

        resp = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        item = resp.data["itens"][0]
        self.assertEqual(Decimal(item["indicador_compra"]["estoque_atual"]), Decimal("20.000"))
        self.assertEqual(item["indicador_compra"]["cor"], "VERDE")

        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "5.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=self.loja).saldo, Decimal("15.000"))
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=self.produto, loja=loja_filial).saldo, Decimal("0.000"))
        self.assertEqual(Decimal(resp.data["qtd_atendida"]), Decimal("5.000"))
        mov = ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"REQ {req.numero}").latest("id")
        self.assertEqual(mov.loja_id, self.loja.id)
        self.assertIn(loja_filial.nome_loja, mov.destino)
        self.client.force_authenticate(self.solicitante)

    def test_uso_consumo_nao_atende_com_estoque_apenas_na_loja_solicitante(self):
        loja_filial = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Saldo Local", apelido_loja="Saldo Local", cnpj="11111111000525", estado="SP")
        self.solicitante.lojas.add(loja_filial)
        self.aprovador.lojas.add(loja_filial)
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("0.000")})
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=loja_filial, defaults={"saldo": Decimal("20.000")})
        req = self.criar_requisicao(loja=loja_filial.id, tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, "5.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "5.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["disponivel"], "0")
        self.client.force_authenticate(self.solicitante)

    def test_uso_consumo_estoque_parcial_e_cotacao_existente_ficam_amarelos(self):
        ProdutoUsoConsumoEstoque.objects.update_or_create(empresa=self.empresa, produto=self.produto, loja=self.loja, defaults={"saldo": Decimal("8.000")})
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, "20.000")
        item = RequisicaoItem.objects.get(pk=item_id)
        cotacao = Cotacao.objects.create(empresa=self.empresa, loja=req.loja, responsavel=self.aprovador, prioridade="NORMAL", tipo_compra="USO_CONSUMO")
        CotacaoItem.objects.create(cotacao=cotacao, produto=self.produto, descricao=self.produto.descricao, quantidade_cotar=Decimal("12.000"), unidade=self.unidade, requisicao_item_origem=item, origem="REQUISICAO")
        resp = self.client.get(f"/api/compras/requisicoes/{req.id}/")
        indicador = resp.data["itens"][0]["indicador_compra"]
        self.assertEqual(Decimal(indicador["estoque_atual"]), Decimal("8.000"))
        self.assertEqual(indicador["cor"], "AMARELO")

    def test_uso_consumo_almoxarifado_sem_loja_fisica_retorna_erro_no_atendimento(self):
        self.almoxarifado.loja = None
        self.almoxarifado.save(update_fields=["loja"])
        req = self.criar_requisicao(tipo_requisicao="USO_CONSUMO")
        item_id = self.item_produto(req, "1.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Não foi possível identificar o estoque do Almoxarifado", str(resp.data))
        self.client.force_authenticate(self.solicitante)

    def test_item_sem_estoque_pode_aguardar_cotacao(self):
        req = self.criar_requisicao()
        item_id = self.item_produto(req, "20.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
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
        self.client.force_authenticate(self.aprovador)
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
        self.assertEqual(resp.data["status"], "DEVOLVIDA_CORRECAO")
        self.client.force_authenticate(self.solicitante)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/rejeitar/", {"motivo": "Sem necessidade"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "REJEITADA")

    def test_aprovador_analisa_sem_editar_conteudo_e_atendente_ve_fila(self):
        req = self.criar_requisicao()
        self.item_produto(req)
        self.client.post(f"/api/compras/requisicoes/{req.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "para_analisar"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])
        resp = self.client.patch(f"/api/compras/requisicoes/{req.id}/", {"observacoes": "Aprovador editou"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.get("/api/compras/requisicoes/", {"visao": "para_atender"})
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(req.id, [r["id"] for r in rows])

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
        self.assertEqual(resp.status_code, 403, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)

        req2 = self.criar_requisicao()
        item2_id = self.item_produto(req2)
        self.client.post(f"/api/compras/requisicoes/{req2.id}/enviar/", {}, format="json")
        self.client.force_authenticate(self.aprovador)
        resp = self.client.post(f"/api/compras/requisicoes/{req2.id}/rejeitar/", {"motivo": "Não aprovado"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post(f"/api/compras/requisicoes/{req2.id}/aprovar/", {}, format="json")
        self.assertEqual(resp.status_code, 404, resp.data)
        self.client.force_authenticate(self.solicitante)
        resp = self.client.post(f"/api/compras/requisicao-itens/{item2_id}/atender/", {"quantidade": "1.000"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_atendimento_nao_permite_item_cancelado_rejeitado_ou_sem_pendente(self):
        req = self.criar_requisicao()
        cancelado_id = self.item_produto(req, "1.000")
        rejeitado_id = self.item_produto(req, "1.000")
        atendido_id = self.item_produto(req, "1.000")
        self.aprovar(req)
        self.client.force_authenticate(self.aprovador)
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

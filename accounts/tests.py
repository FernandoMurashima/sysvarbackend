import json
from io import StringIO
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Cliente, Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from cadastros.models import EmpresaContrato, ModuloSistema
from accounts.models import PerfilAcesso, PerfilModuloPermissao, SessaoUsuario, SessionToken, UserModulePermission
from accounts.services.effective_access import EffectiveAccessService
from accounts.services.sessions import ConcurrentSessionService, token_hash
from auditoria.models import AuditAction, AuditLog
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
        self.empresa_a = Empresa.objects.create(nome="Empresa Isolamento A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa Isolamento B", documento="22222222000102", plano_completo=True)
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
        cpf_cliente = "52998224725" if sufixo == "A" else "39053344705"
        cliente = Cliente.objects.create(
            empresa=empresa,
            nome_cliente=f"Cliente Isolamento {sufixo}",
            apelido=f"CLI{sufixo}",
            cpf=cpf_cliente,
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


class SaaSAccessControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa SaaS", documento="55555555000155", plano_completo=True)
        self.master = get_user_model().objects.create_user(
            username="master_saas",
            password="12345678",
            type="Admin",
            empresa=self.empresa,
        )
        self.operacional = ModuloSistema.objects.get(chave="operacional")
        self.config = ModuloSistema.objects.get(chave="configuracoes")
        self.vendas = ModuloSistema.objects.get(chave="vendas")
        self.perfil_padrao = PerfilAcesso.objects.create(empresa=self.empresa, nome="Operador", padrao=True)
        PerfilModuloPermissao.objects.create(perfil=self.perfil_padrao, modulo=self.operacional, acesso=UserModulePermission.Access.VIEW)
        PerfilModuloPermissao.objects.create(perfil=self.perfil_padrao, modulo=self.config, acesso=UserModulePermission.Access.NONE)
        self.empresa.contrato.usuario_master = self.master
        self.empresa.contrato.save(update_fields=["usuario_master", "updated_at"])

    def active_session(self, user=None, raw="raw-account-token"):
        sessao = SessaoUsuario.objects.create(
            empresa=self.empresa,
            usuario=user or self.master,
            token_key_hash=token_hash(raw),
            dispositivo_id=f"dev-{raw}",
            ultima_atividade_em=timezone.now(),
        )
        SessionToken.objects.create(key_hash=token_hash(raw), session=sessao)
        return raw, sessao

    def test_login_bloqueia_contrato_suspenso(self):
        contrato = self.empresa.contrato
        contrato.status = EmpresaContrato.STATUS_SUSPENSO
        contrato.save(update_fields=["status", "updated_at"])

        response = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "dev-suspenso"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "CONTRACT_SUSPENDED")
        self.assertIn("temporariamente suspenso", str(response.data))

    def test_usuario_ativo_nao_consume_licenca_ate_fazer_login(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        self.client.force_authenticate(self.master)

        response = self.client.post(
            "/api/accounts/users/",
            {
                "username": "novo_usuario",
                "password": "12345678",
                "type": "Regular",
                "Idempresa": self.empresa.pk,
                "perfil_principal_id": self.perfil_padrao.pk,
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        contrato.refresh_from_db()
        self.assertEqual(contrato.sessoes_ativas, 0)

    def test_limite_de_sessoes_bloqueia_login_concorrente(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        get_user_model().objects.create_user(
            username="outro_login",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )

        first = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "dev-1"}, format="json")
        second = self.client.post("/api/accounts/auth/token/", {"username": "outro_login", "password": "12345678", "device_id": "dev-2"}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 403)
        self.assertIn("CONCURRENT_SESSION_LIMIT_REACHED", str(second.data))
        self.assertEqual(SessaoUsuario.objects.filter(empresa=self.empresa, ativa=True).count(), 1)

    def test_logout_encerra_sessao_e_libera_licenca(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])

        login = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "dev-logout"}, format="json")
        self.assertEqual(login.status_code, 200)
        token = login.data["token"]
        self.assertEqual(SessaoUsuario.objects.filter(empresa=self.empresa, ativa=True).count(), 1)

        logout = self.client.post("/api/accounts/auth/logout/", {}, HTTP_AUTHORIZATION=f"Token {token}", format="json")

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(SessaoUsuario.objects.filter(empresa=self.empresa, ativa=True).count(), 0)

    def test_dois_acessos_permitidos_atualizam_indicador_central(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 2
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        joao = get_user_model().objects.create_user(
            username="joao_limite",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )

        fernando_login = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome"}, format="json")
        joao_login = self.client.post("/api/accounts/auth/token/", {"username": joao.username, "password": "12345678", "device_id": "edge"}, format="json")
        indicador = self.client.get(f"/api/cadastros/empresas/{self.empresa.pk}/contrato/", HTTP_AUTHORIZATION=f"Token {fernando_login.data['token']}")

        self.assertEqual(fernando_login.status_code, 200)
        self.assertEqual(joao_login.status_code, 200)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 2)
        self.assertEqual(indicador.data["sessoes_ativas"], 2)
        self.assertEqual(indicador.data["sessoes_disponiveis"], 0)

    def test_terceiro_login_bloqueado_nao_altera_sessoes_tokens_ou_indicador(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 2
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        joao = get_user_model().objects.create_user(username="joao_terceiro", password="12345678", type="Regular", empresa=self.empresa, perfil_principal=self.perfil_padrao)
        maria = get_user_model().objects.create_user(username="maria_terceiro", password="12345678", type="Regular", empresa=self.empresa, perfil_principal=self.perfil_padrao)

        first = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome"}, format="json")
        second = self.client.post("/api/accounts/auth/token/", {"username": joao.username, "password": "12345678", "device_id": "edge"}, format="json")
        valid_tokens_before = SessionToken.objects.filter(session__empresa=self.empresa, revoked_at__isnull=True).count()
        blocked = self.client.post("/api/accounts/auth/token/", {"username": maria.username, "password": "12345678", "device_id": "firefox"}, format="json")
        indicador = self.client.get(f"/api/cadastros/empresas/{self.empresa.pk}/contrato/", HTTP_AUTHORIZATION=f"Token {first.data['token']}")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.data["code"], "CONCURRENT_SESSION_LIMIT_REACHED")
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 2)
        self.assertEqual(valid_tokens_before, SessionToken.objects.filter(session__empresa=self.empresa, revoked_at__isnull=True).count())
        self.assertFalse(SessaoUsuario.objects.filter(usuario=maria, ativa=True).exists())
        self.assertEqual(indicador.data["sessoes_ativas"], 2)

    def test_login_bloqueado_nao_deixa_fantasma_para_usuario_negado(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        joao = get_user_model().objects.create_user(username="joao_bloqueado", password="12345678", type="Regular", empresa=self.empresa, perfil_principal=self.perfil_padrao)

        first = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome"}, format="json")
        blocked = self.client.post("/api/accounts/auth/token/", {"username": joao.username, "password": "12345678", "device_id": "edge"}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 1)
        self.assertFalse(SessaoUsuario.objects.filter(usuario=joao, ativa=True).exists())
        self.assertFalse(SessionToken.objects.filter(session__usuario=joao, revoked_at__isnull=True).exists())

    def test_logout_endpoint_real_revoga_token_e_libera_vaga_no_indicador(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 2
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        login = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome-logout-real"}, format="json")
        token = login.data["token"]
        sessao = SessaoUsuario.objects.get(session_id=login.data["session_id"])

        logout = self.client.post("/api/accounts/auth/logout/", {}, HTTP_AUTHORIZATION=f"Token {token}", format="json")
        self.client.force_authenticate(self.master)
        indicador = self.client.get(f"/api/cadastros/empresas/{self.empresa.pk}/contrato/")

        self.assertEqual(logout.status_code, 200)
        sessao.refresh_from_db()
        self.assertFalse(sessao.ativa)
        self.assertIsNotNone(sessao.encerrada_em)
        self.assertEqual(sessao.motivo_encerramento, "LOGOUT")
        self.assertIsNotNone(SessionToken.objects.get(session=sessao).revoked_at)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 0)
        self.assertEqual(indicador.data["sessoes_ativas"], 0)
        self.assertEqual(indicador.data["sessoes_disponiveis"], 2)

    def test_login_apos_logout_nao_acumula_sessao_fantasma(self):
        first = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome-relogin"}, format="json")
        self.client.post("/api/accounts/auth/logout/", {}, HTTP_AUTHORIZATION=f"Token {first.data['token']}", format="json")
        second = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "chrome-relogin"}, format="json")

        self.assertEqual(second.status_code, 200)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 1)
        self.assertEqual(SessaoUsuario.objects.filter(usuario=self.master, motivo_encerramento="LOGOUT").count(), 1)

    def test_mesmo_usuario_e_dispositivo_substitui_somente_sua_sessao(self):
        first = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "device-x"}, format="json")
        second = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "device-x"}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 1)
        self.assertEqual(SessaoUsuario.objects.filter(usuario=self.master, dispositivo_id="device-x", motivo_encerramento="REPLACED").count(), 1)

    def test_usuarios_diferentes_no_mesmo_dispositivo_sao_independentes(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 2
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        joao = get_user_model().objects.create_user(username="joao_mesmo_device", password="12345678", type="Regular", empresa=self.empresa, perfil_principal=self.perfil_padrao)

        fernando_login = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "device-compartilhado"}, format="json")
        joao_login = self.client.post("/api/accounts/auth/token/", {"username": joao.username, "password": "12345678", "device_id": "device-compartilhado"}, format="json")

        self.assertEqual(fernando_login.status_code, 200)
        self.assertEqual(joao_login.status_code, 200)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 2)
        self.assertEqual(SessaoUsuario.objects.filter(dispositivo_id="device-compartilhado", ativa=True).count(), 2)

    def test_token_revogado_nao_ocupa_vaga_e_reconciliacao_corrige_estado(self):
        raw, sessao = self.active_session(self.master, "revoked-token")
        SessionToken.objects.filter(key_hash=token_hash(raw)).update(revoked_at=timezone.now())

        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 0)
        out = StringIO()
        call_command("reconciliar_sessoes_ativas", "--empresa-id", str(self.empresa.pk), "--apply", stdout=out)

        sessao.refresh_from_db()
        self.assertFalse(sessao.ativa)
        self.assertEqual(sessao.motivo_encerramento, "TOKEN_REVOKED")
        self.assertIn("sessoes_corrigidas=1", out.getvalue())

    def test_indicador_e_login_usam_mesmo_criterio_para_token_revogado(self):
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        raw, _sessao = self.active_session(self.master, "revoked-before-login")
        SessionToken.objects.filter(key_hash=token_hash(raw)).update(revoked_at=timezone.now())

        login = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "new-valid-device"}, format="json")

        self.assertEqual(login.status_code, 200)
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 1)

    def test_superusuario_cria_sessao_e_token_sem_consumir_licenca_da_empresa(self):
        root = get_user_model().objects.create_superuser(username="takeshi_test", password="12345678")
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 1
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])

        login = self.client.post("/api/accounts/auth/token/", {"username": root.username, "password": "12345678", "device_id": "root-device"}, format="json")

        self.assertEqual(login.status_code, 200)
        sessao = SessaoUsuario.objects.get(session_id=login.data["session_id"])
        self.assertIsNone(sessao.empresa_id)
        self.assertTrue(SessionToken.objects.filter(session=sessao, revoked_at__isnull=True).exists())
        self.assertEqual(ConcurrentSessionService.count_active_sessions(self.empresa), 0)
        self.assertEqual(self.empresa.contrato.sessoes_ativas, 0)

    def test_admin_sessoes_usuario_lista_detalhes_e_encerramento_individual(self):
        raw, sessao = self.active_session(self.master, "session-admin-token")
        sessao.user_agent = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        sessao.save(update_fields=["user_agent"])
        self.client.force_authenticate(self.master)

        response = self.client.get(f"/api/accounts/users/{self.master.pk}/sessoes/")
        close = self.client.post(f"/api/accounts/sessoes/{sessao.pk}/encerrar/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["status"], "ATIVA")
        self.assertTrue(response.data[0]["token_valido"])
        self.assertEqual(response.data[0]["navegador"], "Chrome")
        self.assertEqual(close.status_code, 200)
        sessao.refresh_from_db()
        self.assertFalse(sessao.ativa)
        self.assertIsNotNone(SessionToken.objects.get(key_hash=token_hash(raw)).revoked_at)

    def test_encerrar_todas_sessoes_usa_criterio_central_e_rollback_obrigatorio(self):
        raw, sessao = self.active_session(self.master, "close-all-valid")
        raw_revoked, sessao_revogada = self.active_session(self.master, "close-all-revoked")
        SessionToken.objects.filter(key_hash=token_hash(raw_revoked)).update(revoked_at=timezone.now())
        self.client.force_authenticate(self.master)

        response = self.client.post(f"/api/accounts/users/{self.master.pk}/encerrar-sessoes/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sessoes_encerradas"], 1)
        sessao.refresh_from_db()
        sessao_revogada.refresh_from_db()
        self.assertFalse(sessao.ativa)
        self.assertTrue(sessao_revogada.ativa)
        self.assertIsNotNone(SessionToken.objects.get(key_hash=token_hash(raw)).revoked_at)

    def test_empresa_lista_apenas_sessoes_que_ocupam_licenca(self):
        _, sessao_valida = self.active_session(self.master, "empresa-valid")
        sessao_valida.user_agent = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        sessao_valida.save(update_fields=["user_agent"])
        raw_revoked, _sessao_revogada = self.active_session(self.master, "empresa-revoked")
        _raw_expired, sessao_expirada = self.active_session(self.master, "empresa-expired")
        superuser = get_user_model().objects.create_superuser(username="takeshi_test", password="12345678")
        SessaoUsuario.objects.create(
            empresa=None,
            usuario=superuser,
            token_key_hash=token_hash("empresa-superuser"),
            dispositivo_id="dev-superuser",
            ultima_atividade_em=timezone.now(),
        )
        SessionToken.objects.filter(key_hash=token_hash(raw_revoked)).update(revoked_at=timezone.now())
        sessao_expirada.ultima_atividade_em = timezone.now() - timezone.timedelta(minutes=120)
        sessao_expirada.save(update_fields=["ultima_atividade_em"])
        self.client.force_authenticate(self.master)

        response = self.client.get(f"/api/accounts/sessoes/?empresa={self.empresa.pk}&ativa=true")
        data = response.data.get("results", response.data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data), ConcurrentSessionService.count_active_sessions(self.empresa))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], sessao_valida.pk)
        self.assertEqual(data[0]["usuario_username"], self.master.username)
        self.assertEqual(data[0]["status"], "ATIVA")
        self.assertEqual(data[0]["navegador"], "Chrome")
        self.assertEqual(data[0]["sistema_operacional"], "Windows")
        self.assertTrue(data[0]["token_valido"])
        self.assertFalse(data[0]["token_revogado"])
        self.assertIn("tempo_conectado_segundos", data[0])

    def test_encerrar_sessoes_rollback_preserva_sessoes_e_tokens(self):
        user = get_user_model().objects.create_user(
            username="sess_rollback",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )
        raw, sessao = self.active_session(user, "rollback-token")
        self.client.force_authenticate(self.master)

        with patch("accounts.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(f"/api/accounts/users/{user.pk}/encerrar-sessoes/", {}, format="json")

        sessao.refresh_from_db()
        self.assertTrue(sessao.ativa)
        self.assertIsNone(SessionToken.objects.get(key_hash=token_hash(raw)).revoked_at)

    def test_encerrar_sessoes_cria_evento_consolidado_unico(self):
        user = get_user_model().objects.create_user(
            username="sess_ok",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )
        self.active_session(user, "ok-token-1")
        self.active_session(user, "ok-token-2")
        self.client.force_authenticate(self.master)

        response = self.client.post(f"/api/accounts/users/{user.pk}/encerrar-sessoes/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sessoes_encerradas"], 2)
        self.assertEqual(SessaoUsuario.objects.filter(usuario=user, ativa=True).count(), 0)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.USER_SESSIONS_CLOSED, object_id=str(user.pk)).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.SESSION_CLOSED, user=user).count(), 0)

    def test_redefinir_senha_rollback_preserva_senha_flag_sessoes_e_token(self):
        user = get_user_model().objects.create_user(
            username="pwd_rollback",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )
        raw, sessao = self.active_session(user, "pwd-rollback-token")
        self.client.force_authenticate(self.master)

        with patch("accounts.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(
                    f"/api/accounts/users/{user.pk}/redefinir-senha/",
                    {"nova_senha": "NovaSenha123", "confirmacao": "NovaSenha123", "encerrar_sessoes": True},
                    format="json",
                )

        user.refresh_from_db()
        sessao.refresh_from_db()
        self.assertTrue(user.check_password("12345678"))
        self.assertFalse(user.deve_trocar_senha)
        self.assertTrue(sessao.ativa)
        self.assertIsNone(SessionToken.objects.get(key_hash=token_hash(raw)).revoked_at)

    def test_troca_obrigatoria_bloqueia_endpoints_e_libera_apos_alterar(self):
        user = get_user_model().objects.create_user(
            username="pwd_required",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
            deve_trocar_senha=True,
        )
        login = self.client.post("/api/accounts/auth/token/", {"username": user.username, "password": "12345678", "device_id": "pwd-1"}, format="json")
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["deve_trocar_senha"])
        token = login.data["token"]

        blocked = self.client.get("/api/accounts/modulos/", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.data["code"], "PASSWORD_CHANGE_REQUIRED")
        self.assertEqual(self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Token {token}").status_code, 200)

        invalid = self.client.post(
            "/api/accounts/change-required-password/",
            {"senha_atual": "errada", "nova_senha": "NovaSenha123", "confirmacao": "NovaSenha123"},
            HTTP_AUTHORIZATION=f"Token {token}",
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        same = self.client.post(
            "/api/accounts/change-required-password/",
            {"senha_atual": "12345678", "nova_senha": "12345678", "confirmacao": "12345678"},
            HTTP_AUTHORIZATION=f"Token {token}",
            format="json",
        )
        self.assertEqual(same.status_code, 400)
        changed = self.client.post(
            "/api/accounts/change-required-password/",
            {"senha_atual": "12345678", "nova_senha": "NovaSenha123", "confirmacao": "NovaSenha123"},
            HTTP_AUTHORIZATION=f"Token {token}",
            format="json",
        )
        self.assertEqual(changed.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.deve_trocar_senha)
        self.assertEqual(self.client.get("/api/accounts/modulos/", HTTP_AUTHORIZATION=f"Token {token}").status_code, 200)

    def test_troca_obrigatoria_rollback_preserva_senha_e_flag(self):
        user = get_user_model().objects.create_user(
            username="pwd_required_rollback",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
            deve_trocar_senha=True,
        )
        login = self.client.post("/api/accounts/auth/token/", {"username": user.username, "password": "12345678", "device_id": "pwd-rb"}, format="json")
        token = login.data["token"]
        with patch("accounts.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(
                    "/api/accounts/change-required-password/",
                    {"senha_atual": "12345678", "nova_senha": "NovaSenha123", "confirmacao": "NovaSenha123"},
                    HTTP_AUTHORIZATION=f"Token {token}",
                    format="json",
                )
        user.refresh_from_db()
        self.assertTrue(user.check_password("12345678"))
        self.assertTrue(user.deve_trocar_senha)

    def test_perfil_padrao_deve_ser_unico_por_empresa_via_endpoint(self):
        outro = PerfilAcesso.objects.create(empresa=self.empresa, nome="Outro", padrao=False)
        self.client.force_authenticate(self.master)

        response = self.client.post(f"/api/accounts/perfis/{outro.pk}/definir-padrao/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PerfilAcesso.objects.get(pk=outro.pk).padrao)
        self.assertFalse(PerfilAcesso.objects.get(pk=self.perfil_padrao.pk).padrao)

    def test_perfis_usam_operacional_e_validam_dependencias(self):
        dependente = ModuloSistema.objects.create(
            chave="relatorio_financeiro_teste",
            nome="Relatório Financeiro Teste",
            categoria=ModuloSistema.CATEGORIA_COMERCIAL,
            basico=False,
            ativo=True,
            ordem=900,
            dependencias=["financeiro"],
        )
        self.client.force_authenticate(self.master)
        response = self.client.post(
            "/api/accounts/perfis/",
            {
                "empresa": self.empresa.pk,
                "nome": "Perfil Dependente",
                "permissoes_modulos": [
                    {"modulo": dependente.pk, "acesso": UserModulePermission.Access.EDIT},
                    {"modulo": self.operacional.pk, "acesso": UserModulePermission.Access.EDIT},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("depend", str(response.data).lower())

    def test_perfil_rollback_quando_auditoria_obrigatoria_falha(self):
        self.client.force_authenticate(self.master)
        with patch("accounts.serializers.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(
                    "/api/accounts/perfis/",
                    {
                        "empresa": self.empresa.pk,
                        "nome": "Perfil Rollback",
                        "permissoes_modulos": [
                            {"modulo": self.operacional.pk, "acesso": UserModulePermission.Access.EDIT},
                        ],
                    },
                    format="json",
                )

        self.assertFalse(PerfilAcesso.objects.filter(nome="Perfil Rollback").exists())

    def test_crud_comum_nao_inativa_master(self):
        self.client.force_authenticate(self.master)

        response = self.client.patch(f"/api/accounts/users/{self.master.pk}/", {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 400)
        self.master.refresh_from_db()
        self.assertTrue(self.master.is_active)

    def test_transferencia_master_por_endpoint_empresa(self):
        novo = get_user_model().objects.create_user(
            username="novo_master",
            password="12345678",
            type="Admin",
            empresa=self.empresa,
        )
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 5
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        self.client.force_authenticate(self.master)

        response = self.client.post(
            f"/api/cadastros/empresas/{self.empresa.pk}/transferir-master/",
            {"novo_master_id": novo.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.empresa.contrato.refresh_from_db()
        self.assertEqual(self.empresa.contrato.usuario_master_id, novo.pk)

    def test_permissao_efetiva_usa_perfil_e_override(self):
        user = get_user_model().objects.create_user(
            username="operador_saas",
            password="12345678",
            type="Regular",
            empresa=self.empresa,
            perfil_principal=self.perfil_padrao,
        )
        UserModulePermission.objects.create(user=user, modulo="operacional", acesso=UserModulePermission.Access.EDIT)

        access = EffectiveAccessService(user)

        self.assertEqual(access.module_access("operacional"), UserModulePermission.Access.EDIT)
        self.assertEqual(access.module_access("configuracoes"), UserModulePermission.Access.NONE)
        self.assertEqual(access.module_access("vendas"), UserModulePermission.Access.NONE)

    def test_admin_empresa_nao_lista_perfis_de_modulos_nao_contratados(self):
        empresa = Empresa.objects.create(nome="Empresa Perfil Limitado", documento="66666666000166", usa_vendas=True)
        master = get_user_model().objects.create_user(
            username="master_perfil_limitado",
            password="12345678",
            type="Admin",
            empresa=empresa,
        )
        contrato = empresa.contrato
        contrato.plano_completo = False
        contrato.save(update_fields=["plano_completo", "updated_at"])
        self.client.force_authenticate(master)

        response = self.client.get("/api/accounts/perfis/")
        data = response.data.get("results", response.data)
        nomes = {item["nome"] for item in data}

        self.assertEqual(response.status_code, 200)
        self.assertIn("Administrador delegado", nomes)
        self.assertIn("Regular", nomes)
        self.assertNotIn("Financeiro", nomes)
        self.assertNotIn("Compras", nomes)
        self.assertNotIn("Estoque", nomes)
        self.assertNotIn("Fiscal", nomes)

    def test_superusuario_altera_contrato_por_empresa_e_incrementa_versao(self):
        superuser = get_user_model().objects.create_superuser(username="root_contract", password="12345678")
        contrato = self.empresa.contrato
        versao = contrato.permissions_version
        self.client.force_authenticate(superuser)

        response = self.client.patch(
            f"/api/cadastros/empresas/{self.empresa.pk}/contrato/",
            {"limite_sessoes_simultaneas": 5, "status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.limite_sessoes_simultaneas, 5)
        self.assertTrue(contrato.plano_completo)
        self.assertGreater(contrato.permissions_version, versao)

    def test_master_consulta_mas_nao_altera_contrato(self):
        self.client.force_authenticate(self.master)

        detail = self.client.get(f"/api/cadastros/empresas/{self.empresa.pk}/contrato/")
        update = self.client.patch(
            f"/api/cadastros/empresas/{self.empresa.pk}/contrato/",
            {"limite_sessoes_simultaneas": 9},
            format="json",
        )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(update.status_code, 403)

    def test_reducao_limite_retorna_excedente_sem_desativar(self):
        superuser = get_user_model().objects.create_superuser(username="root_reduce", password="12345678")
        contrato = self.empresa.contrato
        contrato.limite_sessoes_simultaneas = 5
        contrato.save(update_fields=["limite_sessoes_simultaneas", "updated_at"])
        get_user_model().objects.create_user(username="ativo_extra", password="12345678", empresa=self.empresa, type="Regular", perfil_principal=self.perfil_padrao)
        self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "reduce-1"}, format="json")
        self.client.post("/api/accounts/auth/token/", {"username": "ativo_extra", "password": "12345678", "device_id": "reduce-2"}, format="json")
        self.client.force_authenticate(superuser)

        response = self.client.patch(
            f"/api/cadastros/empresas/{self.empresa.pk}/contrato/",
            {"limite_sessoes_simultaneas": 1},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["limite_excedido"])
        self.assertEqual(response.data["excedente"], 1)
        self.assertIn("acima do limite", response.data["warning"])
        self.assertEqual(self.empresa.usuarios.filter(is_active=True, is_superuser=False).count(), 2)
        self.assertEqual(SessaoUsuario.objects.filter(empresa=self.empresa, ativa=True).count(), 2)

    def test_contrato_ativo_limite_zero_rejeitado(self):
        superuser = get_user_model().objects.create_superuser(username="root_zero", password="12345678")
        self.client.force_authenticate(superuser)

        response = self.client.patch(
            f"/api/cadastros/empresas/{self.empresa.pk}/contrato/",
            {"status": EmpresaContrato.STATUS_ATIVO, "limite_sessoes_simultaneas": 0},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("limite_sessoes_simultaneas", response.data)

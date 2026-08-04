import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Cliente, Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from cadastros.models import EmpresaContrato, ModuloSistema
from accounts.models import PerfilAcesso, PerfilModuloPermissao, SessaoUsuario, UserModulePermission
from accounts.services.effective_access import EffectiveAccessService
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

    def test_login_bloqueia_contrato_suspenso(self):
        contrato = self.empresa.contrato
        contrato.status = EmpresaContrato.STATUS_SUSPENSO
        contrato.save(update_fields=["status", "updated_at"])

        response = self.client.post("/api/accounts/auth/token/", {"username": "master_saas", "password": "12345678", "device_id": "dev-suspenso"})

        self.assertEqual(response.status_code, 401)
        self.assertIn("Contrato suspenso", str(response.data))

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

    def test_perfil_padrao_deve_ser_unico_por_empresa_via_endpoint(self):
        outro = PerfilAcesso.objects.create(empresa=self.empresa, nome="Outro", padrao=False)
        self.client.force_authenticate(self.master)

        response = self.client.post(f"/api/accounts/perfis/{outro.pk}/definir-padrao/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PerfilAcesso.objects.get(pk=outro.pk).padrao)
        self.assertFalse(PerfilAcesso.objects.get(pk=self.perfil_padrao.pk).padrao)

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

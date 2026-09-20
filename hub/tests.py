from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CredencialPdvUsuario, PerfilAcesso
from cadastros.models import Cargo, Cliente, Empresa, Funcionarios, Loja, Nat_Lancamento
from financeiro.models import Caixa, ContaBancaria, FormaPagamento, FormaPagamentoParcela, PrazoPagamento, TipoDespesaPdv
from fiscal.models import VendaPdv, VendaPdvItem, VendaPdvPagamento
from financeiro.models import MovimentacaoFinanceira, Receber
from hub.models import (
    AtivacaoSysvarHub,
    HubClienteMapeamento,
    HubEventoRecebido,
    HubFechamentoDiaRecebido,
    HubMovimentoCaixaRecebido,
    HubSessaoCaixaRecebida,
    HubVendaMapeamento,
    SysvarHub,
)
from hub.sync import HubSyncProcessor, payload_hash
from produto.models import EstoqueMovimentacao
from produto.models import ConfigEan, Cor, Estoque, Grade, Produto, ProdutoDetalhe, ProdutoImagem, Tabelapreco, TabelaprecoProduto, Tamanho, Unidade


class SysvarHubModelTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Hub", documento="11222333000181")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Hub",
            apelido_loja="HUB",
            cnpj="11222333000181",
            estado="SP",
        )

    def test_loja_possui_somente_um_hub(self):
        SysvarHub.objects.create(loja=self.loja)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SysvarHub.objects.create(loja=self.loja, nome="Outro Hub")

    def test_gerar_token_nao_salva_token_puro(self):
        hub = SysvarHub.objects.create(loja=self.loja)

        token = hub.gerar_token()
        hub.refresh_from_db()

        self.assertNotEqual(hub.token_hash, token)
        self.assertEqual(hub.token_prefixo, token[:12])
        self.assertFalse(SysvarHub.objects.filter(token_hash=token).exists())

    def test_hash_do_token_e_validavel(self):
        hub = SysvarHub.objects.create(loja=self.loja)

        token = hub.gerar_token()
        hub.refresh_from_db()

        self.assertEqual(hub.token_hash, SysvarHub.hash_token(token))

    def test_vinculo_hub_loja_empresa_permanece_correto(self):
        hub = SysvarHub.objects.create(loja=self.loja)

        self.assertEqual(hub.loja_id, self.loja.id)
        self.assertEqual(hub.loja.empresa_id, self.empresa.id)
        self.assertEqual(hub.loja.empresa, self.empresa)
        self.assertFalse(hasattr(hub, "empresa_id"))


class AtivacaoSysvarHubModelTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Ativacao Hub", documento="21222333000181")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Ativacao Hub",
            apelido_loja="AHUB",
            cnpj="21222333000181",
            estado="SP",
        )

    def test_codigo_gerado_nao_e_armazenado_em_texto_puro(self):
        ativacao, codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)

        self.assertNotEqual(ativacao.codigo_hash, codigo)
        self.assertEqual(ativacao.codigo_prefixo, codigo[:4])
        self.assertFalse(AtivacaoSysvarHub.objects.filter(codigo_hash=codigo).exists())

    def test_hash_reconhece_codigo_original(self):
        ativacao, codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)

        self.assertEqual(ativacao.codigo_hash, AtivacaoSysvarHub.hash_codigo(codigo))
        self.assertEqual(ativacao.codigo_hash, AtivacaoSysvarHub.hash_codigo(codigo.lower()))

    def test_codigo_recem_criado_esta_utilizavel(self):
        ativacao, codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)

        self.assertIsNone(ativacao.hub_id)
        self.assertTrue(ativacao.esta_utilizavel())
        self.assertTrue(ativacao.expira_em > timezone.now())
        self.assertEqual(ativacao.codigo_hash, AtivacaoSysvarHub.hash_codigo(codigo))

    def test_codigo_expirado_nao_esta_utilizavel(self):
        ativacao, _codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)
        ativacao.expira_em = timezone.now() - timedelta(minutes=1)

        self.assertFalse(ativacao.esta_utilizavel())

    def test_codigo_usado_nao_esta_utilizavel(self):
        ativacao, _codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)
        ativacao.usado_em = timezone.now()

        self.assertFalse(ativacao.esta_utilizavel())

    def test_codigo_revogado_nao_esta_utilizavel(self):
        ativacao, _codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)
        ativacao.revogado_em = timezone.now()

        self.assertFalse(ativacao.esta_utilizavel())

    def test_ativacao_esta_vinculada_a_loja_correta(self):
        ativacao, _codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)

        self.assertEqual(ativacao.loja_id, self.loja.id)
        self.assertEqual(ativacao.loja, self.loja)

    def test_empresa_e_obtida_pela_loja_sem_campo_duplicado(self):
        ativacao, _codigo = AtivacaoSysvarHub.criar(loja=self.loja, criado_por=None)

        self.assertEqual(ativacao.loja.empresa_id, self.empresa.id)
        self.assertEqual(ativacao.loja.empresa, self.empresa)
        self.assertFalse(hasattr(ativacao, "empresa_id"))


class SysvarHubApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa API Hub", documento="31222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Empresa API Hub", documento="41222333000181")
        self.user = get_user_model().objects.create_user(
            "hub-admin",
            "hub-admin@sysvar.test",
            "123",
            empresa=self.empresa,
            type="Admin",
        )
        self.outro_user = get_user_model().objects.create_user(
            "hub-admin-outro",
            "hub-admin-outro@sysvar.test",
            "123",
            empresa=self.outra_empresa,
            type="Admin",
        )
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja API Hub",
            apelido_loja="APIH",
            cnpj="31222333000181",
            estado="SP",
        )
        self.outra_loja = Loja.objects.create(
            empresa=self.outra_empresa,
            nome_loja="Outra Loja API Hub",
            apelido_loja="OAPI",
            cnpj="41222333000181",
            estado="SP",
        )

    def _admin(self, user=None):
        self.client.credentials()
        self.client.force_authenticate(user or self.user)

    def _criar_codigo(self, loja=None):
        ativacao, codigo = AtivacaoSysvarHub.criar(loja=loja or self.loja, criado_por=self.user)
        return ativacao, codigo

    def _ativar(self, codigo, hub_uuid="11111111-1111-4111-8111-111111111111", **extras):
        payload = {
            "codigo": codigo,
            "hub_uuid": hub_uuid,
            "nome": "Hub Loja",
            "hostname": "HOST-HUB",
            "versao": "1.0.0",
        }
        payload.update(extras)
        return self.client.post("/api/hub/ativar/", payload, format="json", REMOTE_ADDR="10.0.0.10")

    def _hub_autenticado(self, loja=None, hub_uuid="77777777-7777-4777-8777-777777777777", ativo=True, versao="1.2.3"):
        hub = SysvarHub.objects.create(
            loja=loja or self.loja,
            hub_uuid=hub_uuid,
            nome="Hub Bootstrap",
            hostname="HOST-BOOT",
            versao=versao,
            ativo=ativo,
        )
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub, token

    def test_usuario_autorizado_consegue_gerar_codigo_para_sua_loja(self):
        self._admin()

        resp = self.client.post("/api/hub/ativacoes/", {"loja": self.loja.id}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn("codigo", resp.data)
        self.assertEqual(resp.data["loja_id"], self.loja.id)
        ativacao = AtivacaoSysvarHub.objects.get(pk=resp.data["id"])
        self.assertEqual(ativacao.loja_id, self.loja.id)
        self.assertEqual(ativacao.codigo_hash, AtivacaoSysvarHub.hash_codigo(resp.data["codigo"]))
        self.assertNotEqual(ativacao.codigo_hash, resp.data["codigo"])

    def test_usuario_nao_consegue_gerar_codigo_para_loja_de_outra_empresa(self):
        self._admin()

        resp = self.client.post("/api/hub/ativacoes/", {"loja": self.outra_loja.id}, format="json")

        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertFalse(AtivacaoSysvarHub.objects.exists())

    def test_ativacao_valida_cria_e_vincula_hub_sem_permitir_loja_por_payload(self):
        ativacao, codigo = self._criar_codigo()
        self.client.force_authenticate(user=None)

        resp = self._ativar(codigo, loja_id=self.outra_loja.id, empresa_id=self.outra_empresa.id)

        self.assertEqual(resp.status_code, 200, resp.data)
        hub = SysvarHub.objects.get(pk=resp.data["hub_id"])
        ativacao.refresh_from_db()
        self.assertEqual(hub.loja_id, self.loja.id)
        self.assertEqual(hub.loja.empresa_id, self.empresa.id)
        self.assertEqual(resp.data["loja_id"], self.loja.id)
        self.assertEqual(resp.data["empresa_id"], self.empresa.id)
        self.assertEqual(ativacao.hub_id, hub.id)
        self.assertIsNotNone(ativacao.usado_em)
        self.assertEqual(hub.hostname, "HOST-HUB")
        self.assertEqual(hub.versao, "1.0.0")
        self.assertEqual(hub.ultimo_ip, "10.0.0.10")

    def test_codigo_invalido_expirado_usado_e_revogado_sao_rejeitados(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self._ativar("AAAA-BBBB-CCCC").status_code, 400)

        expirado, codigo_expirado = self._criar_codigo()
        expirado.expira_em = timezone.now() - timedelta(minutes=1)
        expirado.save(update_fields=["expira_em"])
        self.assertEqual(self._ativar(codigo_expirado, "22222222-2222-4222-8222-222222222222").status_code, 400)

        usado, codigo_usado = self._criar_codigo()
        usado.usado_em = timezone.now()
        usado.save(update_fields=["usado_em"])
        self.assertEqual(self._ativar(codigo_usado, "33333333-3333-4333-8333-333333333333").status_code, 400)

        revogado, codigo_revogado = self._criar_codigo()
        revogado.revogado_em = timezone.now()
        revogado.save(update_fields=["revogado_em"])
        self.assertEqual(self._ativar(codigo_revogado, "44444444-4444-4444-8444-444444444444").status_code, 400)

    def test_token_puro_nao_fica_armazenado_e_autenticacao_hub_funciona(self):
        _ativacao, codigo = self._criar_codigo()
        self.client.force_authenticate(user=None)

        resp = self._ativar(codigo)

        self.assertEqual(resp.status_code, 200, resp.data)
        token = resp.data["token"]
        self.assertNotIn("token_hash", resp.data)
        hub = SysvarHub.objects.get(pk=resp.data["hub_id"])
        self.assertNotEqual(hub.token_hash, token)
        self.assertEqual(hub.token_hash, SysvarHub.hash_token(token))

        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        heartbeat = self.client.post("/api/hub/heartbeat/", {"hostname": "HOST-2", "versao": "1.0.1"}, format="json")
        self.assertEqual(heartbeat.status_code, 200, heartbeat.data)
        hub.refresh_from_db()
        self.assertIsNotNone(hub.ultimo_contato)
        self.assertEqual(hub.hostname, "HOST-2")
        self.assertEqual(hub.versao, "1.0.1")

    def test_token_invalido_e_heartbeat_sem_autenticacao_sao_rejeitados(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()
        self.assertIn(self.client.post("/api/hub/heartbeat/", {}, format="json").status_code, (401, 403))

        self.client.credentials(HTTP_AUTHORIZATION="Hub token-invalido")
        self.assertIn(self.client.post("/api/hub/heartbeat/", {}, format="json").status_code, (401, 403))

    def test_nova_ativacao_da_mesma_loja_rotaciona_token_e_invalida_anterior(self):
        _ativacao, codigo = self._criar_codigo()
        self.client.force_authenticate(user=None)
        primeiro = self._ativar(codigo)
        self.assertEqual(primeiro.status_code, 200, primeiro.data)
        token_antigo = primeiro.data["token"]
        hub_id = primeiro.data["hub_id"]

        _nova_ativacao, novo_codigo = self._criar_codigo()
        segundo = self._ativar(novo_codigo, "55555555-5555-4555-8555-555555555555", hostname="HOST-NOVO")

        self.assertEqual(segundo.status_code, 200, segundo.data)
        self.assertEqual(segundo.data["hub_id"], hub_id)
        self.assertNotEqual(segundo.data["token"], token_antigo)
        hub = SysvarHub.objects.get(pk=hub_id)
        self.assertEqual(str(hub.hub_uuid), "55555555-5555-4555-8555-555555555555")
        self.assertEqual(hub.hostname, "HOST-NOVO")

        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token_antigo}")
        self.assertIn(self.client.post("/api/hub/heartbeat/", {}, format="json").status_code, (401, 403))

        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {segundo.data['token']}")
        self.assertEqual(self.client.post("/api/hub/heartbeat/", {}, format="json").status_code, 200)

    def test_hub_uuid_de_outra_loja_nao_pode_ser_reutilizado(self):
        hub_uuid = "66666666-6666-4666-8666-666666666666"
        SysvarHub.objects.create(loja=self.outra_loja, hub_uuid=hub_uuid)
        _ativacao, codigo = self._criar_codigo()
        self.client.force_authenticate(user=None)

        resp = self._ativar(codigo, hub_uuid)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertFalse(SysvarHub.objects.filter(loja=self.loja).exists())

    def test_bootstrap_autenticado_retorna_identidade_canonica_do_hub(self):
        self.empresa.nome = "Razao Social API Hub"
        self.empresa.nome_fantasia = "Fantasia Empresa API"
        self.empresa.save(update_fields=["nome", "nome_fantasia"])
        self.loja.logradouro = "Rua"
        self.loja.endereco = "Rua Fiscal"
        self.loja.numero = "123"
        self.loja.complemento = "Sala 4"
        self.loja.bairro = "Centro"
        self.loja.cidade = "Sao Paulo"
        self.loja.estado = "SP"
        self.loja.cep = "01001000"
        self.loja.codigo_municipio_ibge = "3550308"
        self.loja.emite_nfce = True
        self.loja.ambiente_fiscal = Empresa.AMBIENTE_HOMOLOGACAO
        self.loja.regime_tributario = Empresa.REGIME_SIMPLES
        self.loja.inscricao_estadual = "110042490114"
        self.loja.serie_nfce = 7
        self.loja.proximo_numero_nfce = 1234
        self.loja.save()
        hub, _token = self._hub_autenticado()

        resp = self.client.get("/api/hub/bootstrap/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["bootstrap_versao"], 1)
        self.assertIn("servidor_em", resp.data)
        self.assertEqual(resp.data["hub"]["id"], hub.id)
        self.assertEqual(resp.data["hub"]["hub_uuid"], str(hub.hub_uuid))
        self.assertEqual(resp.data["hub"]["versao"], "1.2.3")
        self.assertEqual(resp.data["empresa"]["id"], self.empresa.id)
        self.assertEqual(resp.data["empresa"]["nome"], self.empresa.nome)
        self.assertEqual(resp.data["loja"]["id"], self.loja.id)
        self.assertEqual(resp.data["loja"]["nome_loja"], self.loja.nome_loja)
        self.assertEqual(resp.data["loja"]["apelido_loja"], self.loja.apelido_loja)
        self.assertEqual(resp.data["loja"]["cnpj"], self.loja.cnpj)
        self.assertEqual(resp.data["loja"]["estado"], self.loja.estado)
        self.assertEqual(resp.data["loja"]["fiscal"], {
            "emite_nfce": True,
            "ambiente_fiscal": Empresa.AMBIENTE_HOMOLOGACAO,
            "regime_tributario": Empresa.REGIME_SIMPLES,
            "inscricao_estadual": "110042490114",
            "serie_nfce": 7,
            "proximo_numero_nfce": 1234,
            "razao_social": "Razao Social API Hub",
            "nome_fantasia": "Fantasia Empresa API",
            "cnpj": self.loja.cnpj,
            "logradouro": "Rua",
            "endereco": "Rua Fiscal",
            "numero": "123",
            "complemento": "Sala 4",
            "bairro": "Centro",
            "cidade": "Sao Paulo",
            "estado": "SP",
            "uf": "SP",
            "cep": "01001000",
            "codigo_municipio_ibge": "3550308",
        })
        for termo in ["certificado", "pfx", "p12", "senha", "chave_privada", "csc", "token_csc"]:
            self.assertNotIn(termo, str(resp.data).lower())

    def test_bootstrap_fiscal_usa_fallbacks_e_aceita_opcionais_vazios(self):
        self.empresa.nome_fantasia = ""
        self.empresa.save(update_fields=["nome_fantasia"])
        self.loja.nome_loja = "Loja Fallback"
        self.loja.inscricao_estadual = None
        self.loja.logradouro = None
        self.loja.endereco = None
        self.loja.numero = None
        self.loja.complemento = None
        self.loja.bairro = None
        self.loja.cidade = None
        self.loja.estado = None
        self.loja.cep = None
        self.loja.codigo_municipio_ibge = None
        self.loja.save()
        self._hub_autenticado()

        resp = self.client.get("/api/hub/bootstrap/")

        self.assertEqual(resp.status_code, 200, resp.data)
        fiscal = resp.data["loja"]["fiscal"]
        self.assertEqual(fiscal["razao_social"], self.empresa.nome)
        self.assertEqual(fiscal["nome_fantasia"], "Loja Fallback")
        self.assertIsNone(fiscal["inscricao_estadual"])
        self.assertIsNone(fiscal["logradouro"])
        self.assertIsNone(fiscal["endereco"])
        self.assertIsNone(fiscal["numero"])
        self.assertIsNone(fiscal["complemento"])
        self.assertIsNone(fiscal["bairro"])
        self.assertIsNone(fiscal["cidade"])
        self.assertIsNone(fiscal["estado"])
        self.assertIsNone(fiscal["uf"])
        self.assertIsNone(fiscal["cep"])
        self.assertIsNone(fiscal["codigo_municipio_ibge"])

    def test_bootstrap_nao_cria_empresa_ou_loja(self):
        empresas_antes = Empresa.objects.count()
        lojas_antes = Loja.objects.count()
        self._hub_autenticado()

        resp = self.client.get("/api/hub/bootstrap/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Empresa.objects.count(), empresas_antes)
        self.assertEqual(Loja.objects.count(), lojas_antes)

    def test_bootstrap_retorna_somente_caixas_ativos_da_loja_operacionais(self):
        self._hub_autenticado()
        caixa_b = Caixa.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="002",
            descricao="Caixa B",
            ativo=True,
            saldo_inicial=100,
            saldo_atual=150,
            conta_contabil="1.1.1",
        )
        caixa_a = Caixa.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="001",
            descricao="Caixa A",
            ativo=True,
        )
        Caixa.objects.create(
            empresa=self.outra_empresa,
            idloja=self.outra_loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="003",
            descricao="Caixa Outra Loja",
            ativo=True,
        )
        Caixa.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="004",
            descricao="Caixa Inativo",
            ativo=False,
        )
        Caixa.objects.create(
            empresa=self.empresa,
            idloja=None,
            tipo_caixa=Caixa.TIPO_MASTER,
            codigo="MASTER",
            descricao="Caixa Master",
            ativo=True,
        )

        resp = self.client.get("/api/hub/bootstrap/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            resp.data["caixas"],
            [
                {"id": caixa_a.Idcaixa, "codigo": "001", "descricao": "Caixa A", "ativo": True},
                {"id": caixa_b.Idcaixa, "codigo": "002", "descricao": "Caixa B", "ativo": True},
            ],
        )
        self.assertNotIn("saldo_inicial", resp.data["caixas"][0])
        self.assertNotIn("saldo_atual", resp.data["caixas"][0])
        self.assertNotIn("conta_contabil", resp.data["caixas"][0])

    def test_bootstrap_query_string_nao_altera_escopo_da_loja(self):
        self._hub_autenticado()
        caixa_loja = Caixa.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="001",
            descricao="Caixa Loja Hub",
            ativo=True,
        )
        Caixa.objects.create(
            empresa=self.outra_empresa,
            idloja=self.outra_loja,
            tipo_caixa=Caixa.TIPO_LOJA,
            codigo="999",
            descricao="Caixa Outra Loja",
            ativo=True,
        )

        resp = self.client.get(
            f"/api/hub/bootstrap/?loja_id={self.outra_loja.id}&empresa_id={self.outra_empresa.id}"
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["empresa"]["id"], self.empresa.id)
        self.assertEqual(resp.data["loja"]["id"], self.loja.id)
        self.assertEqual(resp.data["loja"]["fiscal"]["cnpj"], self.loja.cnpj)
        self.assertNotEqual(resp.data["loja"]["fiscal"]["cnpj"], self.outra_loja.cnpj)
        self.assertEqual([caixa["id"] for caixa in resp.data["caixas"]], [caixa_loja.Idcaixa])

    def test_token_invalido_e_bootstrap_sem_autenticacao_sao_rejeitados(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()
        self.assertIn(self.client.get("/api/hub/bootstrap/").status_code, (401, 403))

        self.client.credentials(HTTP_AUTHORIZATION="Hub token-invalido")
        self.assertIn(self.client.get("/api/hub/bootstrap/").status_code, (401, 403))

    def test_hub_inativo_nao_acessa_bootstrap(self):
        self._hub_autenticado(ativo=False)

        resp = self.client.get("/api/hub/bootstrap/")

        self.assertIn(resp.status_code, (401, 403))


class SysvarHubCatalogoApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Catalogo Hub", documento="51222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Empresa Catalogo Hub", documento="61222333000181")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Catalogo",
            apelido_loja="CAT",
            cnpj="51222333000181",
            estado="SP",
        )
        self.outra_loja_mesma_empresa = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Outra Loja Mesma Empresa",
            apelido_loja="OLM",
            cnpj="51222333000182",
            estado="SP",
        )
        self.loja_outra_empresa = Loja.objects.create(
            empresa=self.outra_empresa,
            nome_loja="Loja Outra Empresa",
            apelido_loja="OE",
            cnpj="61222333000181",
            estado="SP",
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Codigo="UN", Descricao="UNIDADE")
        self.grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        self.cor = Cor.objects.create(empresa=self.empresa, Descricao="AZUL", Codigo="AZ", Cor="Azul")
        self.tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="40", Descricao="40")
        self.config_ean = ConfigEan.objects.create(empresa=self.empresa, company_prefix="1234")
        self.tabela_padrao = Tabelapreco.objects.create(
            empresa=self.empresa,
            NomeTabela="Tabela Padrão",
            DataInicio=timezone.localdate(),
        )

    def _hub_autenticado(self, loja=None, ativo=True):
        hub = SysvarHub.objects.create(
            loja=loja or self.loja,
            hub_uuid="88888888-8888-4888-8888-888888888888" if loja != self.outra_loja_mesma_empresa else "99999999-9999-4999-8999-999999999999",
            ativo=ativo,
        )
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _produto(self, descricao="Produto Catalogo", tipo="1", empresa=None, ativo=True, bloqueado=False, ref="26-01-01001"):
        return Produto.objects.create(
            empresa=empresa or self.empresa,
            tipo_produto=tipo,
            referencia=ref,
            descricao=descricao,
            descricao_reduzida=descricao[:60],
            unidade=self.unidade,
            ativo=ativo,
            bloqueado_venda=bloqueado,
            ncm="6204.62.00",
            origem_mercadoria=0,
            cfop_venda_dentro="5102",
            cfop_venda_fora="6102",
            csosn_ou_cst_icms="102",
            aliquota_icms=Decimal("12.00"),
            cst_pis="01",
            aliq_pis=Decimal("1.65"),
            cst_cofins="01",
            aliq_cofins=Decimal("7.60"),
        )

    def _sku(self, produto, ativo=True, bloqueado=False, ean=None):
        sku = ProdutoDetalhe.objects.create(
            produto=produto,
            idcor=self.cor,
            idtamanho=self.tamanho,
            ativo=ativo,
            bloqueado_venda=bloqueado,
        )
        if ean is not None:
            ProdutoDetalhe.objects.filter(pk=sku.pk).update(ean13=ean)
            sku.refresh_from_db()
        return sku

    def _preco(self, produto, tabela=None, preco="199.9000", promocional=None):
        return TabelaprecoProduto.objects.create(
            produto=produto,
            tabela=tabela or self.tabela_padrao,
            preco=Decimal(preco),
            preco_promocional=Decimal(promocional) if promocional is not None else None,
            DataInicio=timezone.localdate(),
            ativo=True,
        )

    def _estoque(self, sku, loja=None, estoque="5.000", reserva="1.000"):
        return Estoque.objects.create(
            CodigodeBarra=sku.ean13,
            referencia=sku.produto.referencia or "",
            Idloja=loja or self.loja,
            Estoque=Decimal(estoque),
            reserva=Decimal(reserva),
        )

    def _catalogo(self, query=""):
        return self.client.get(f"/api/hub/catalogo/{query}")

    def test_catalogo_autenticado_retorna_contrato_e_item_vendavel(self):
        hub = self._hub_autenticado()
        produto = self._produto()
        sku = self._sku(produto)
        self._preco(produto)
        self._estoque(sku)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["catalogo_versao"], 2)
        self.assertIn("gerado_em", resp.data)
        self.assertEqual(resp.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(resp.data["empresa"]["id"], self.empresa.id)
        self.assertEqual(resp.data["loja"]["id"], self.loja.id)
        self.assertEqual(resp.data["loja"]["nome"], "Loja Catalogo")
        self.assertEqual(resp.data["loja"]["apelido"], "CAT")
        self.assertEqual(resp.data["tabela_preco"]["codigo"], "PADRAO")
        self.assertEqual(resp.data["tabela_preco"]["id"], self.tabela_padrao.pk)
        self.assertEqual(resp.data["tabela_preco"]["nome"], "Tabela Padrão")
        self.assertEqual(resp.data["total_itens"], 1)
        item = resp.data["itens"][0]
        self.assertEqual(item["produto_id"], produto.pk)
        self.assertEqual(item["sku_id"], sku.pk)
        self.assertEqual(item["tipo_produto"], "1")
        self.assertEqual(item["ean13"], sku.ean13)
        self.assertEqual(item["codigo_item_ref"], sku.codigo_item_ref)
        self.assertEqual(item["cor"], {"id": self.cor.pk, "descricao": "AZUL"})
        self.assertEqual(item["tamanho"], {"id": self.tamanho.pk, "descricao": "40"})
        self.assertEqual(item["unidade"], {"id": self.unidade.pk, "codigo": "UN", "descricao": "UNIDADE"})
        self.assertEqual(item["preco"], Decimal("199.9000"))
        self.assertIsNone(item["preco_promocional"])
        self.assertEqual(item["preco_venda"], Decimal("199.9000"))
        self.assertEqual(item["estoque_fisico"], Decimal("5.000"))
        self.assertEqual(item["reserva"], Decimal("1.000"))
        self.assertEqual(item["estoque_disponivel"], Decimal("4.000"))
        self.assertTrue(item["vendavel"])
        self.assertEqual(item["motivos_bloqueio"], [])
        self.assertEqual(item["fiscal"]["ncm"], "6204.62.00")
        self.assertEqual(item["fiscal"]["origem_mercadoria"], 0)
        self.assertEqual(item["fiscal"]["cfop_venda_dentro"], "5102")
        self.assertEqual(item["fiscal"]["cfop_venda_fora"], "6102")
        self.assertEqual(item["fiscal"]["csosn_ou_cst_icms"], "102")
        self.assertEqual(item["fiscal"]["aliquota_icms"], Decimal("12.00"))
        self.assertEqual(item["fiscal"]["cst_pis"], "01")
        self.assertEqual(item["fiscal"]["aliq_pis"], Decimal("1.65"))
        self.assertEqual(item["fiscal"]["cst_cofins"], "01")
        self.assertEqual(item["fiscal"]["aliq_cofins"], Decimal("7.60"))
        self.assertIsNone(item["imagem"])
        self.assertNotIn("imagem_url", item)

    def test_catalogo_envia_imagem_principal_preferindo_reduzida(self):
        self._hub_autenticado()
        produto = self._produto()
        sku = self._sku(produto)
        self._preco(produto)
        self._estoque(sku)
        secundaria = ProdutoImagem.objects.create(produto=produto, principal=False, ordem=1)
        secundaria.imagem.save("secundaria.jpg", ContentFile(b"original"), save=True)
        principal = ProdutoImagem.objects.create(produto=produto, principal=True, ordem=2)
        principal.imagem.save("principal.jpg", ContentFile(b"original"), save=False)
        principal.imagem_reduzida.save("principal.webp", ContentFile(b"reduzida"), save=True)

        resp = self._catalogo()

        imagem = resp.data["itens"][0]["imagem"]
        self.assertEqual(imagem["id"], principal.pk)
        self.assertEqual(imagem["tipo"], "reduzida")
        self.assertEqual(imagem["versao"], principal.atualizado_em.isoformat())

    def test_catalogo_sem_principal_usa_ordem_e_id_deterministicos(self):
        self._hub_autenticado()
        produto = self._produto()
        sku = self._sku(produto)
        self._preco(produto)
        self._estoque(sku)
        segunda = ProdutoImagem.objects.create(produto=produto, ordem=2)
        segunda.imagem.save("segunda.jpg", ContentFile(b"segunda"), save=True)
        primeira = ProdutoImagem.objects.create(produto=produto, ordem=1)
        primeira.imagem.save("primeira.jpg", ContentFile(b"primeira"), save=True)

        resp = self._catalogo()

        self.assertEqual(resp.data["itens"][0]["imagem"]["id"], primeira.pk)
        self.assertEqual(resp.data["itens"][0]["imagem"]["tipo"], "original")

    def test_download_imagem_aceita_accept_image_wildcard(self):
        self._hub_autenticado()
        produto = self._produto()
        imagem = ProdutoImagem.objects.create(produto=produto, principal=True)
        imagem.imagem.save("foto-hub.png", ContentFile(b"\x89PNG\r\n\x1a\nfoto-central"), save=True)

        resp = self.client.get(f"/api/hub/catalogo/imagens/{imagem.pk}/", HTTP_ACCEPT="image/*")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"\x89PNG\r\n\x1a\nfoto-central")
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_download_imagem_sem_accept_especifico_continua_funcionando(self):
        self._hub_autenticado()
        produto = self._produto()
        imagem = ProdutoImagem.objects.create(produto=produto, principal=True)
        imagem.imagem.save("foto.jpg", ContentFile(b"foto-central"), save=True)

        resp = self.client.get(f"/api/hub/catalogo/imagens/{imagem.pk}/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"foto-central")
        self.assertEqual(resp["Content-Type"], "image/jpeg")

    def test_download_imagem_exige_hub_e_respeita_empresa(self):
        hub = self._hub_autenticado()
        produto = self._produto()
        imagem = ProdutoImagem.objects.create(produto=produto, principal=True)
        imagem.imagem.save("foto.jpg", ContentFile(b"foto-central"), save=True)

        ok = self.client.get(f"/api/hub/catalogo/imagens/{imagem.pk}/")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(b"".join(ok.streaming_content), b"foto-central")

        self.client.credentials()
        self.assertIn(self.client.get(f"/api/hub/catalogo/imagens/{imagem.pk}/").status_code, (401, 403))

        outro_hub = SysvarHub.objects.create(loja=self.loja_outra_empresa, hub_uuid="77777777-7777-4777-8777-777777777777", ativo=True)
        token = outro_hub.gerar_token()
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        self.assertEqual(self.client.get(f"/api/hub/catalogo/imagens/{imagem.pk}/").status_code, 404)

    def test_catalogo_sem_authorization_token_invalido_e_hub_inativo_sao_rejeitados(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()
        self.assertIn(self._catalogo().status_code, (401, 403))
        self.client.credentials(HTTP_AUTHORIZATION="Hub token-invalido")
        self.assertIn(self._catalogo().status_code, (401, 403))
        self._hub_autenticado(ativo=False)
        self.assertIn(self._catalogo().status_code, (401, 403))

    def test_escopo_vem_do_hub_e_query_string_nao_altera_loja_ou_empresa(self):
        self._hub_autenticado()
        produto = self._produto()
        sku = self._sku(produto)
        self._preco(produto)
        self._estoque(sku, self.loja, "2.000", "0.000")
        self._estoque(sku, self.outra_loja_mesma_empresa, "100.000", "0.000")

        resp = self._catalogo(f"?loja_id={self.outra_loja_mesma_empresa.id}&empresa_id={self.outra_empresa.id}")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["empresa"]["id"], self.empresa.id)
        self.assertEqual(resp.data["loja"]["id"], self.loja.id)
        self.assertEqual(resp.data["itens"][0]["estoque_fisico"], Decimal("2.000"))
        self.assertEqual(resp.data["itens"][0]["estoque_disponivel"], Decimal("2.000"))

    def test_filtros_operacionais_e_escopo_de_empresa(self):
        self._hub_autenticado()
        incluidos = []
        for tipo, ref in (("1", "26-01-01011"), ("3", "26-01-01013")):
            produto = self._produto(f"Tipo {tipo}", tipo=tipo, ref=ref)
            sku = self._sku(produto)
            self._preco(produto)
            self._estoque(sku)
            incluidos.append(sku.pk)

        casos_excluidos = [
            self._produto("Tipo nao vendavel", tipo="2", ref="USO-000001"),
            self._produto("Produto inativo", ativo=False, ref="26-01-01014"),
            self._produto("Produto bloqueado", bloqueado=True, ref="26-01-01015"),
        ]
        for produto in casos_excluidos:
            self._sku(produto)
        produto_sku_inativo = self._produto("SKU inativo", ref="26-01-01016")
        self._sku(produto_sku_inativo, ativo=False)
        produto_sku_bloqueado = self._produto("SKU bloqueado", ref="26-01-01017")
        self._sku(produto_sku_bloqueado, bloqueado=True)

        unidade_outra = Unidade.objects.create(empresa=self.outra_empresa, Codigo="UN", Descricao="UNIDADE")
        produto_outra_empresa = Produto.objects.create(
            empresa=self.outra_empresa,
            tipo_produto="1",
            referencia="26-01-01999",
            descricao="Outra Empresa",
            unidade=unidade_outra,
            ativo=True,
        )
        cor_outra = Cor.objects.create(empresa=self.outra_empresa, Descricao="PRETO", Codigo="PR", Cor="Preto")
        grade_outra = Grade.objects.create(empresa=self.outra_empresa, Descricao="Grade Outra")
        tamanho_outra = Tamanho.objects.create(empresa=self.outra_empresa, idgrade=grade_outra, Tamanho="P", Descricao="P")
        ConfigEan.objects.create(empresa=self.outra_empresa, company_prefix="5678")
        ProdutoDetalhe.objects.create(produto=produto_outra_empresa, idcor=cor_outra, idtamanho=tamanho_outra)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([item["sku_id"] for item in resp.data["itens"]], incluidos)
        self.assertEqual(resp.data["total_itens"], 2)

    def test_sem_preco_sem_estoque_ausencia_de_estoque_e_sku_sem_ean_nao_quebram_snapshot(self):
        self._hub_autenticado()
        produto_sem_preco = self._produto("Sem Preco", ref="26-01-02001")
        sku_sem_preco = self._sku(produto_sem_preco)
        self._estoque(sku_sem_preco)
        produto_sem_estoque = self._produto("Sem Estoque", ref="26-01-02002")
        sku_sem_estoque = self._sku(produto_sem_estoque)
        self._preco(produto_sem_estoque)
        self._estoque(sku_sem_estoque, estoque="0.000", reserva="0.000")
        produto_sem_registro = self._produto("Sem Registro Estoque", ref="26-01-02003")
        sku_sem_registro = self._sku(produto_sem_registro)
        self._preco(produto_sem_registro)
        produto_sem_ean = self._produto("Sem EAN", ref="26-01-02004")
        sku_sem_ean = self._sku(produto_sem_ean, ean="")
        self._preco(produto_sem_ean)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        itens = {item["sku_id"]: item for item in resp.data["itens"]}
        self.assertFalse(itens[sku_sem_preco.pk]["vendavel"])
        self.assertEqual(itens[sku_sem_preco.pk]["motivos_bloqueio"], ["SEM_PRECO"])
        self.assertIsNone(itens[sku_sem_preco.pk]["preco_venda"])
        self.assertFalse(itens[sku_sem_estoque.pk]["vendavel"])
        self.assertEqual(itens[sku_sem_estoque.pk]["motivos_bloqueio"], ["SEM_ESTOQUE"])
        self.assertFalse(itens[sku_sem_registro.pk]["vendavel"])
        self.assertEqual(itens[sku_sem_registro.pk]["estoque_fisico"], Decimal("0.000"))
        self.assertEqual(itens[sku_sem_registro.pk]["reserva"], Decimal("0.000"))
        self.assertEqual(itens[sku_sem_registro.pk]["estoque_disponivel"], Decimal("0.000"))
        self.assertEqual(itens[sku_sem_registro.pk]["motivos_bloqueio"], ["SEM_ESTOQUE"])
        self.assertEqual(itens[sku_sem_ean.pk]["sku_id"], sku_sem_ean.pk)
        self.assertIsNone(itens[sku_sem_ean.pk]["ean13"])
        self.assertEqual(itens[sku_sem_ean.pk]["motivos_bloqueio"], ["SEM_ESTOQUE"])

    def test_tabela_padrao_empresa_validade_promocional_e_ausencia_de_tabela_padrao(self):
        self._hub_autenticado()
        produto = self._produto(ref="26-01-03001")
        sku = self._sku(produto)
        outra_tabela = Tabelapreco.objects.create(empresa=self.empresa, NomeTabela="ATACADO", DataInicio=timezone.localdate())
        TabelaprecoProduto.objects.create(produto=produto, tabela=outra_tabela, preco=Decimal("399.9000"), DataInicio=timezone.localdate(), ativo=True)
        tabela_outra_empresa = Tabelapreco.objects.create(empresa=self.outra_empresa, NomeTabela="Tabela Padrão", DataInicio=timezone.localdate())
        TabelaprecoProduto.objects.create(produto=produto, tabela=tabela_outra_empresa, preco=Decimal("299.9000"), DataInicio=timezone.localdate(), ativo=True)
        self._preco(produto, preco="199.9000", promocional="149.9000")
        self._estoque(sku)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        item = resp.data["itens"][0]
        self.assertEqual(resp.data["tabela_preco"]["codigo"], "PADRAO")
        self.assertEqual(resp.data["tabela_preco"]["nome"], "Tabela Padrão")
        self.assertEqual(item["preco"], Decimal("199.9000"))
        self.assertEqual(item["preco_promocional"], Decimal("149.9000"))
        self.assertEqual(item["preco_venda"], Decimal("149.9000"))

        TabelaprecoProduto.objects.filter(tabela=self.tabela_padrao).delete()
        self.tabela_padrao.delete()
        resp_sem_tabela_padrao = self._catalogo()

        self.assertEqual(resp_sem_tabela_padrao.status_code, 200, resp_sem_tabela_padrao.data)
        self.assertIsNone(resp_sem_tabela_padrao.data["tabela_preco"])
        self.assertEqual(resp_sem_tabela_padrao.data["itens"][0]["motivos_bloqueio"], ["SEM_PRECO"])

    def test_multiplas_tabelas_padrao_validas_usa_mais_recente_e_deterministica(self):
        self._hub_autenticado()
        produto = self._produto(ref="26-01-04001")
        sku = self._sku(produto)
        antiga = self.tabela_padrao
        nova = Tabelapreco.objects.create(
            empresa=self.empresa,
            NomeTabela="Tabela Padrão",
            DataInicio=timezone.localdate() + timedelta(days=0),
        )
        self._preco(produto, tabela=antiga, preco="100.0000")
        self._preco(produto, tabela=nova, preco="200.0000")
        self._estoque(sku)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["tabela_preco"]["id"], nova.pk)
        self.assertEqual(resp.data["itens"][0]["preco_venda"], Decimal("200.0000"))

    def test_tabela_padrao_futura_ou_expirada_nao_e_utilizada(self):
        self._hub_autenticado()
        produto = self._produto(ref="26-01-04002")
        sku = self._sku(produto)
        futura = Tabelapreco.objects.create(
            empresa=self.empresa,
            NomeTabela="Tabela Padrão",
            DataInicio=timezone.localdate() + timedelta(days=1),
        )
        expirada = Tabelapreco.objects.create(
            empresa=self.empresa,
            NomeTabela="Tabela Padrão",
            DataInicio=timezone.localdate() - timedelta(days=10),
            DataFim=timezone.localdate() - timedelta(days=1),
        )
        self._preco(produto, tabela=self.tabela_padrao, preco="120.0000")
        self._preco(produto, tabela=futura, preco="220.0000")
        self._preco(produto, tabela=expirada, preco="320.0000")
        self._estoque(sku)

        resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["tabela_preco"]["id"], self.tabela_padrao.pk)
        self.assertEqual(resp.data["tabela_preco"]["codigo"], "PADRAO")
        self.assertEqual(resp.data["itens"][0]["preco_venda"], Decimal("120.0000"))

    def test_catalogo_evitar_n_mais_um_em_cenario_controlado(self):
        self._hub_autenticado()
        for idx in range(3):
            produto = self._produto(f"Produto {idx}", ref=f"26-01-05{idx:03d}")
            sku = self._sku(produto)
            self._preco(produto)
            self._estoque(sku)

        with self.assertNumQueries(6):
            resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["total_itens"], 3)


class SysvarHubOperadoresApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Operadores Hub", documento="81222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Operadores Hub", documento="91222333000181")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Operadores", apelido_loja="OP", cnpj="81222333000181", estado="SP")
        self.outra_loja_mesma_empresa = Loja.objects.create(empresa=self.empresa, nome_loja="Outra Loja Operadores", apelido_loja="OOP", cnpj="81222333000182", estado="SP")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Operadores", apelido_loja="OOE", cnpj="91222333000181", estado="SP")
        self.perfil, _created = PerfilAcesso.objects.get_or_create(empresa=self.empresa, nome="Operador Hub PDV")

    def _hub_autenticado(self, loja=None, hub_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"):
        hub = SysvarHub.objects.create(loja=loja or self.loja, hub_uuid=hub_uuid, ativo=True)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _usuario(self, username, empresa=None, loja=None, lojas=None, ativo=True, perfil=True, first_name="", last_name=""):
        user = get_user_model().objects.create_user(
            username=username,
            password="12345678",
            type="Caixa",
            empresa=empresa or self.empresa,
            loja=loja,
            is_active=ativo,
            perfil_principal=self.perfil if perfil else None,
            first_name=first_name,
            last_name=last_name,
        )
        if lojas:
            user.lojas.set(lojas)
        if not perfil:
            get_user_model().objects.filter(pk=user.pk).update(perfil_principal=None)
            user.refresh_from_db()
        return user

    def _credencial(self, user, senha="SenhaPdv123", habilitado=True):
        return CredencialPdvUsuario.objects.create(usuario=user, senha_hash=make_password(senha), habilitado=habilitado)

    def test_operadores_exige_autenticacao_hub(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()

        response = self.client.get("/api/hub/operadores/")

        self.assertIn(response.status_code, (401, 403))

    def test_snapshot_retorna_apenas_operadores_elegiveis_da_loja_do_hub(self):
        hub = self._hub_autenticado()
        por_loja = self._usuario("caixa_loja", loja=self.loja, first_name="Caixa", last_name="Loja")
        por_lojas = self._usuario("caixa_lojas", lojas=[self.loja], perfil=False)
        sem_nome = self._usuario("caixa_sem_nome", loja=self.loja)
        sem_credencial = self._usuario("sem_credencial", loja=self.loja)
        cred_removida = self._usuario("cred_removida", loja=self.loja)
        inativo = self._usuario("inativo", loja=self.loja, ativo=False)
        outra_empresa = self._usuario("outra_empresa", empresa=self.outra_empresa, loja=self.loja_outra_empresa)
        sem_acesso_loja = self._usuario("sem_acesso_loja", loja=self.outra_loja_mesma_empresa)
        for user in [por_loja, por_lojas, sem_nome, cred_removida, inativo, outra_empresa, sem_acesso_loja]:
            self._credencial(user)
        cred_removida.credencial_pdv.delete()

        response = self.client.get(f"/api/hub/operadores/?loja_id={self.outra_loja_mesma_empresa.pk}&empresa_id={self.outra_empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["operadores_versao"], 1)
        self.assertIn("gerado_em", response.data)
        self.assertEqual(response.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(response.data["empresa"], {"id": self.empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja.pk})
        operadores = {item["codigo"]: item for item in response.data["operadores"]}
        self.assertEqual(set(operadores), {"caixa_loja", "caixa_lojas", "caixa_sem_nome"})
        self.assertEqual(operadores["caixa_loja"]["nome"], "Caixa Loja")
        self.assertEqual(operadores["caixa_sem_nome"]["nome"], "caixa_sem_nome")
        self.assertEqual(operadores["caixa_loja"]["perfil"], {"id": self.perfil.pk, "nome": "Operador Hub PDV"})
        self.assertIsNone(operadores["caixa_lojas"]["perfil"])
        self.assertTrue(operadores["caixa_loja"]["ativo"])
        self.assertNotIn("senha", operadores["caixa_loja"])
        self.assertIn("credencial_hash", operadores["caixa_loja"])
        self.assertTrue(check_password("SenhaPdv123", operadores["caixa_loja"]["credencial_hash"]))
        self.assertFalse(check_password("senha-errada", operadores["caixa_loja"]["credencial_hash"]))
        self.assertFalse(CredencialPdvUsuario.objects.filter(usuario=sem_credencial).exists())

    def test_credencial_desabilitada_nao_aparece(self):
        self._hub_autenticado()
        user = self._usuario("cred_desabilitada", loja=self.loja)
        self._credencial(user, habilitado=False)

        response = self.client.get("/api/hub/operadores/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["operadores"], [])

    def test_operadores_evita_n_mais_um_em_cenario_controlado(self):
        self._hub_autenticado()
        for idx in range(3):
            user = self._usuario(f"caixa_query_{idx}", loja=self.loja)
            self._credencial(user)

        with self.assertNumQueries(2):
            response = self.client.get("/api/hub/operadores/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["operadores"]), 3)


class SysvarHubVendedoresApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Vendedores Hub", documento="81222333000191")
        self.outra_empresa = Empresa.objects.create(nome="Outra Vendedores Hub", documento="91222333000191")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Vendedores", apelido_loja="VD", cnpj="81222333000191", estado="SP")
        self.outra_loja_mesma_empresa = Loja.objects.create(empresa=self.empresa, nome_loja="Outra Loja Vendedores", apelido_loja="OVD", cnpj="81222333000192", estado="SP")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Vendedores", apelido_loja="VOE", cnpj="91222333000191", estado="SP")
        self.cargo = Cargo.objects.create(
            empresa=self.empresa,
            codigo="VEND",
            descricao="Vendedor",
            ativo=True,
            participa_vendas=True,
            permite_comissao=True,
        )
        self.cargo_outra_empresa = Cargo.objects.create(
            empresa=self.outra_empresa,
            codigo="VEND",
            descricao="Vendedor Outra Empresa",
            ativo=True,
            participa_vendas=True,
            permite_comissao=True,
        )
        self._cpf_seq = 10000000000

    def _hub_autenticado(self, loja=None, hub_uuid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"):
        hub = SysvarHub.objects.create(loja=loja or self.loja, hub_uuid=hub_uuid, ativo=True)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _funcionario(self, nome, empresa=None, loja=None, cargo=None, **kwargs):
        self._cpf_seq += 1
        defaults = {
            "empresa": empresa if empresa is not None else self.empresa,
            "idloja": loja if loja is not None else self.loja,
            "cargo": cargo if cargo is not None else self.cargo,
            "matricula": str(self._cpf_seq)[-6:],
            "nomefuncionario": nome,
            "apelido": nome[:20],
            "cpf": str(self._cpf_seq),
            "ativo": True,
            "situacao": Funcionarios.SITUACAO_ATIVO,
            "participa_vendas": True,
            "comissionado": True,
            "comissao_percentual": Decimal("3.00"),
            "salario": Decimal("2500.00"),
            "telefone": "11999990000",
            "whatsapp": "11999990001",
            "email": f"{self._cpf_seq}@example.com",
            "endereco": "Rua Sigilosa",
        }
        defaults.update(kwargs)
        return Funcionarios.objects.create(**defaults)

    def test_vendedores_exige_autenticacao_hub(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()

        response = self.client.get("/api/hub/vendedores/")

        self.assertIn(response.status_code, (401, 403))

    def test_snapshot_retorna_apenas_vendedores_elegiveis_da_loja_do_hub(self):
        hub = self._hub_autenticado()
        ana = self._funcionario("Ana Vendedora", matricula="000101", comissao_percentual=Decimal("3.00"))
        bruno = self._funcionario("Bruno Vendedor", matricula="000102", comissao_percentual=Decimal("2.50"))
        self._funcionario("Outra Loja", loja=self.outra_loja_mesma_empresa)
        self._funcionario("Outra Empresa", empresa=self.outra_empresa, loja=self.loja_outra_empresa, cargo=self.cargo_outra_empresa)
        self._funcionario("Nao Participa", participa_vendas=False)
        self._funcionario("Inativo", ativo=False)
        self._funcionario("Afastado", situacao=Funcionarios.SITUACAO_AFASTADO)
        self._funcionario("Desligado", situacao=Funcionarios.SITUACAO_DESLIGADO)

        response = self.client.get(f"/api/hub/vendedores/?loja_id={self.outra_loja_mesma_empresa.pk}&empresa_id={self.outra_empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["vendedores_versao"], 1)
        self.assertIn("gerado_em", response.data)
        self.assertEqual(response.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(response.data["empresa"], {"id": self.empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja.pk})
        self.assertEqual([item["nome"] for item in response.data["vendedores"]], ["Ana Vendedora", "Bruno Vendedor"])

        vendedores = {item["nome"]: item for item in response.data["vendedores"]}
        self.assertEqual(vendedores["Ana Vendedora"]["id"], ana.pk)
        self.assertEqual(vendedores["Ana Vendedora"]["matricula"], "000101")
        self.assertEqual(vendedores["Ana Vendedora"]["apelido"], "Ana Vendedora")
        self.assertEqual(vendedores["Ana Vendedora"]["cargo"], {"id": self.cargo.pk, "codigo": "VEND", "descricao": "Vendedor"})
        self.assertTrue(vendedores["Ana Vendedora"]["comissionado"])
        self.assertEqual(vendedores["Ana Vendedora"]["comissao_percentual"], "3.00")
        self.assertTrue(vendedores["Ana Vendedora"]["ativo"])
        self.assertEqual(vendedores["Ana Vendedora"]["situacao"], Funcionarios.SITUACAO_ATIVO)
        self.assertTrue(vendedores["Ana Vendedora"]["participa_vendas"])
        self.assertEqual(vendedores["Bruno Vendedor"]["id"], bruno.pk)
        self.assertEqual(vendedores["Bruno Vendedor"]["comissao_percentual"], "2.50")

    def test_payload_nao_expoe_campos_sensiveis_de_funcionario(self):
        self._hub_autenticado()
        self._funcionario("Privado Vendedor")

        response = self.client.get("/api/hub/vendedores/")

        self.assertEqual(response.status_code, 200, response.data)
        vendedor = response.data["vendedores"][0]
        campos_sensiveis = {
            "cpf",
            "salario",
            "telefone",
            "whatsapp",
            "email",
            "endereco",
            "inicio",
            "fim",
            "usuario",
            "usuario_id",
            "senha",
            "password",
            "credencial_hash",
            "dados_bancarios",
        }
        for campo in campos_sensiveis:
            self.assertNotIn(campo, vendedor)

    def test_vendedores_evita_n_mais_um_em_cenario_controlado(self):
        self._hub_autenticado()
        for idx in range(3):
            self._funcionario(f"Vendedor Query {idx}")

        with self.assertNumQueries(2):
            response = self.client.get("/api/hub/vendedores/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["vendedores"]), 3)


class SysvarHubClientesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Clientes Hub", documento="81222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Clientes Hub", documento="81222333000182")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Clientes", apelido_loja="CL", cnpj="81222333000181", estado="SP")
        self.outra_loja_mesma_empresa = Loja.objects.create(empresa=self.empresa, nome_loja="Outra Loja Clientes", apelido_loja="OCL", cnpj="81222333000183", estado="SP")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Clientes", apelido_loja="OOC", cnpj="81222333000182", estado="SP")

    def _hub_autenticado(self, loja=None, hub_uuid="dddddddd-dddd-4ddd-8ddd-dddddddddddd"):
        hub = SysvarHub.objects.create(loja=loja or self.loja, hub_uuid=hub_uuid, ativo=True)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _clientes(self, query=""):
        return self.client.get(f"/api/hub/clientes/{query}")

    def _cliente(self, nome, empresa=None, **kwargs):
        defaults = {
            "empresa": empresa if empresa is not None else self.empresa,
            "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
            "documento": None,
            "nome_cliente": nome,
            "ativo": True,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def test_clientes_exige_autenticacao_hub(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()

        response_sem_token = self._clientes()

        self.client.credentials(HTTP_AUTHORIZATION="Hub token-invalido")
        response_token_invalido = self._clientes()

        usuario = get_user_model().objects.create_user(
            username="usuario-comum-clientes",
            password="Senha12345",
            empresa=self.empresa,
        )
        self.client.credentials()
        self.client.force_authenticate(user=usuario)
        response_usuario_comum = self._clientes()

        self.assertIn(response_sem_token.status_code, (401, 403))
        self.assertIn(response_token_invalido.status_code, (401, 403))
        self.assertIn(response_usuario_comum.status_code, (401, 403))

    def test_snapshot_completo_usa_empresa_do_hub_e_serializa_contrato(self):
        hub = self._hub_autenticado()
        cliente_padrao = self._cliente(
            "Consumidor Final",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento=Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
            cliente_padrao=True,
        )
        ativo = self._cliente(
            "Ana Cliente",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="12345678901",
            apelido="Ana",
            endereco="Rua Central",
            numero="10",
            complemento="Sala 1",
            cep="01001000",
            bairro="Centro",
            cidade="Sao Paulo",
            estado="SP",
            telefone1="11999990000",
            telefone2="1133334444",
            email="ana@sysvar.test",
            categoria="VIP",
            aniversario="1990-05-20",
            mala_direta=True,
            aceita_email=True,
            aceita_whatsapp=True,
            aceita_sms=True,
            consentimento_em=timezone.now(),
            origem_consentimento="PDV",
        )
        bloqueado = self._cliente(
            "Bruno Bloqueado",
            tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA,
            documento="11222333000181",
            bloqueio=True,
            motivo_bloqueio="FINANCEIRO",
        )
        inativo = self._cliente("Carlos Inativo", documento="23456789012", ativo=False)
        self._cliente("Outra Empresa", empresa=self.outra_empresa, documento="34567890123")

        response = self._clientes(f"?loja_id={self.outra_loja_mesma_empresa.pk}&empresa_id={self.outra_empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["clientes_versao"], 1)
        self.assertIn("gerado_em", response.data)
        self.assertEqual(response.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(response.data["empresa"], {"id": self.empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja.pk})
        self.assertEqual([item["id"] for item in response.data["clientes"]], [ativo.pk, bloqueado.pk, inativo.pk, cliente_padrao.pk])

        clientes = {item["id"]: item for item in response.data["clientes"]}
        self.assertEqual(set(clientes), {ativo.pk, bloqueado.pk, inativo.pk, cliente_padrao.pk})
        self.assertEqual(clientes[ativo.pk]["tipo_pessoa"], "PF")
        self.assertEqual(clientes[ativo.pk]["documento"], "12345678901")
        self.assertEqual(clientes[ativo.pk]["nome_cliente"], "Ana Cliente")
        self.assertEqual(clientes[ativo.pk]["apelido"], "Ana")
        self.assertEqual(clientes[ativo.pk]["endereco"], "Rua Central")
        self.assertEqual(clientes[ativo.pk]["numero"], "10")
        self.assertEqual(clientes[ativo.pk]["complemento"], "Sala 1")
        self.assertEqual(clientes[ativo.pk]["cep"], "01001000")
        self.assertEqual(clientes[ativo.pk]["bairro"], "Centro")
        self.assertEqual(clientes[ativo.pk]["cidade"], "Sao Paulo")
        self.assertEqual(clientes[ativo.pk]["estado"], "SP")
        self.assertEqual(clientes[ativo.pk]["telefone1"], "11999990000")
        self.assertEqual(clientes[ativo.pk]["telefone2"], "1133334444")
        self.assertEqual(clientes[ativo.pk]["email"], "ana@sysvar.test")
        self.assertEqual(clientes[ativo.pk]["categoria"], "VIP")
        self.assertEqual(str(clientes[ativo.pk]["aniversario"]), "1990-05-20")
        self.assertTrue(clientes[ativo.pk]["mala_direta"])
        self.assertTrue(clientes[ativo.pk]["aceita_email"])
        self.assertTrue(clientes[ativo.pk]["aceita_whatsapp"])
        self.assertTrue(clientes[ativo.pk]["aceita_sms"])
        self.assertIsNotNone(clientes[ativo.pk]["consentimento_em"])
        self.assertEqual(clientes[ativo.pk]["origem_consentimento"], "PDV")
        self.assertTrue(clientes[ativo.pk]["ativo"])

        self.assertEqual(clientes[bloqueado.pk]["tipo_pessoa"], "PJ")
        self.assertEqual(clientes[bloqueado.pk]["documento"], "11222333000181")
        self.assertTrue(clientes[bloqueado.pk]["bloqueio"])
        self.assertEqual(clientes[bloqueado.pk]["motivo_bloqueio"], "FINANCEIRO")
        self.assertFalse(clientes[inativo.pk]["ativo"])
        self.assertTrue(clientes[cliente_padrao.pk]["cliente_padrao"])
        self.assertEqual(clientes[cliente_padrao.pk]["documento"], Cliente.DOCUMENTO_CONSUMIDOR_FINAL)

        payload_texto = str(response.data).lower()
        for termo in ["token", "password", "secret", "bloqueado_por", "observacao_bloqueio", "consentimento_observacao"]:
            self.assertNotIn(termo, payload_texto)

    def test_hub_de_outra_empresa_nao_consegue_alterar_escopo_por_query_string(self):
        self._cliente("Empresa A", documento="12345678901")
        cliente_outra = self._cliente("Empresa B", empresa=self.outra_empresa, documento="11222333000181")
        self._hub_autenticado(loja=self.loja_outra_empresa, hub_uuid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

        response = self._clientes(f"?loja_id={self.loja.pk}&empresa_id={self.empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["empresa"], {"id": self.outra_empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja_outra_empresa.pk})
        self.assertEqual([item["id"] for item in response.data["clientes"]], [cliente_outra.pk])

    def test_get_nao_altera_clientes_e_evita_n_mais_um(self):
        self._hub_autenticado()
        for idx in range(3):
            self._cliente(f"Cliente {idx}", documento=f"1234567890{idx}")
        antes = list(Cliente.objects.filter(empresa=self.empresa).order_by("id").values())

        with self.assertNumQueries(2):
            response = self._clientes()

        depois = list(Cliente.objects.filter(empresa=self.empresa).order_by("id").values())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["clientes"]), 3)
        self.assertEqual(antes, depois)


class SysvarHubFormasPagamentoApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Formas Hub", documento="71222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Formas Hub", documento="71222333000182")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Formas", apelido_loja="FP", cnpj="71222333000181", estado="SP")
        self.outra_loja_mesma_empresa = Loja.objects.create(empresa=self.empresa, nome_loja="Outra Loja Formas", apelido_loja="OFP", cnpj="71222333000183", estado="SP")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Formas", apelido_loja="OOF", cnpj="71222333000182", estado="SP")

    def _hub_autenticado(self, loja=None, hub_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"):
        hub = SysvarHub.objects.create(loja=loja or self.loja, hub_uuid=hub_uuid, ativo=True)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _formas_pagamento(self, query=""):
        return self.client.get(f"/api/hub/formas-pagamento/{query}")

    def _forma(self, codigo, empresa=None, **kwargs):
        defaults = {
            "empresa": empresa if empresa is not None else self.empresa,
            "codigo": codigo,
            "descricao": f"Forma {codigo}",
            "tipo": FormaPagamento.TIPO_OUTRO,
            "num_parcelas": 1,
        }
        defaults.update(kwargs)
        return FormaPagamento.objects.create(**defaults)

    def test_formas_pagamento_exige_autenticacao_hub(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()

        response = self._formas_pagamento()

        self.assertIn(response.status_code, (401, 403))

    def test_snapshot_completo_usa_escopo_do_hub_e_serializa_contrato(self):
        hub = self._hub_autenticado()
        prazo = PrazoPagamento.objects.create(
            empresa=self.empresa,
            codigo="30D",
            descricao="30 dias",
            num_parcelas=2,
            intervalo_dias=30,
        )
        conta = ContaBancaria.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            descricao="Conta liquidação",
            banco="001",
            agencia="1234",
            conta="56789",
        )
        credito = self._forma(
            "002",
            descricao="Cartão Crédito",
            tipo=FormaPagamento.TIPO_CREDITO_PARCELADO,
            num_parcelas=2,
            prazo_pagamento=prazo,
            adquirente="Rede",
            conta_liquidacao=conta,
            gera_recebivel_bancario=True,
            prazo_credito_dias=30,
            taxa_percentual=Decimal("2.5000"),
            taxa_fixa=Decimal("1.20"),
            tef_habilitado=True,
            tef_modalidade="CREDITO",
            tef_adquirente_codigo="REDE",
            tef_terminal_logico="TERM01",
        )
        dinheiro = self._forma(
            "001",
            descricao="Dinheiro",
            tipo=FormaPagamento.TIPO_DINHEIRO,
            ativo=False,
            taxa_percentual=Decimal("0"),
            taxa_fixa=Decimal("0"),
        )
        self._forma("003", empresa=self.outra_empresa, descricao="Outra Empresa")
        FormaPagamento.objects.create(empresa=None, codigo="000", descricao="Sem Empresa", tipo=FormaPagamento.TIPO_OUTRO)
        FormaPagamentoParcela.objects.create(forma=credito, ordem=2, dias=60, percentual=None, valor_fixo=Decimal("100.00"))
        FormaPagamentoParcela.objects.create(forma=credito, ordem=1, dias=30, percentual=Decimal("0.500000"), valor_fixo=None)
        FormaPagamentoParcela.objects.create(forma=dinheiro, ordem=1, dias=0, percentual=Decimal("1.000000"), valor_fixo=None)

        response = self._formas_pagamento(f"?loja_id={self.outra_loja_mesma_empresa.pk}&empresa_id={self.outra_empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["formas_pagamento_versao"], 1)
        self.assertIn("gerado_em", response.data)
        self.assertEqual(response.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(response.data["empresa"], {"id": self.empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja.pk})
        self.assertEqual([item["codigo"] for item in response.data["formas_pagamento"]], ["001", "002"])

        dinheiro_payload = response.data["formas_pagamento"][0]
        self.assertEqual(dinheiro_payload["id"], dinheiro.pk)
        self.assertFalse(dinheiro_payload["ativo"])
        self.assertIsNone(dinheiro_payload["prazo_pagamento"])
        self.assertEqual(dinheiro_payload["taxa_percentual"], "0.0000")
        self.assertEqual(dinheiro_payload["taxa_fixa"], "0.00")
        self.assertEqual(dinheiro_payload["parcelas"][0]["percentual"], "1.000000")
        self.assertIsNone(dinheiro_payload["parcelas"][0]["valor_fixo"])

        credito_payload = response.data["formas_pagamento"][1]
        self.assertEqual(credito_payload["id"], credito.pk)
        self.assertEqual(credito_payload["descricao"], "Cartão Crédito")
        self.assertEqual(credito_payload["tipo"], FormaPagamento.TIPO_CREDITO_PARCELADO)
        self.assertEqual(credito_payload["num_parcelas"], 2)
        self.assertEqual(credito_payload["prazo_pagamento"], {
            "id": prazo.pk,
            "codigo": "30D",
            "descricao": "30 dias",
            "num_parcelas": 2,
            "intervalo_dias": 30,
        })
        self.assertEqual(credito_payload["adquirente"], "Rede")
        self.assertEqual(credito_payload["conta_liquidacao_id"], conta.pk)
        self.assertNotIn("conta_liquidacao", credito_payload)
        self.assertTrue(credito_payload["gera_recebivel_bancario"])
        self.assertEqual(credito_payload["prazo_credito_dias"], 30)
        self.assertEqual(credito_payload["taxa_percentual"], "2.5000")
        self.assertEqual(credito_payload["taxa_fixa"], "1.20")
        self.assertTrue(credito_payload["tef_habilitado"])
        self.assertEqual(credito_payload["tef_modalidade"], "CREDITO")
        self.assertEqual(credito_payload["tef_adquirente_codigo"], "REDE")
        self.assertEqual(credito_payload["tef_terminal_logico"], "TERM01")
        self.assertEqual(credito_payload["parcelas"], [
            {"ordem": 1, "dias": 30, "percentual": "0.500000", "valor_fixo": None},
            {"ordem": 2, "dias": 60, "percentual": None, "valor_fixo": "100.00"},
        ])
        payload_texto = str(response.data).lower()
        for termo in ["token", "password", "secret"]:
            self.assertNotIn(termo, payload_texto)
        self.assertNotIn("hash", payload_texto)

    def test_hub_de_outra_empresa_nao_consegue_alterar_escopo_por_query_string(self):
        self._forma("EMP1")
        forma_outra = self._forma("EMP2", empresa=self.outra_empresa)
        self._hub_autenticado(loja=self.loja_outra_empresa, hub_uuid="cccccccc-cccc-4ccc-8ccc-cccccccccccc")

        response = self._formas_pagamento(f"?loja_id={self.loja.pk}&empresa_id={self.empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["empresa"], {"id": self.outra_empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja_outra_empresa.pk})
        self.assertEqual([item["id"] for item in response.data["formas_pagamento"]], [forma_outra.pk])

    def test_ordenacao_estavel_por_codigo_e_id(self):
        forma_b = self._forma("B")
        forma_a = self._forma("A")
        forma_c = self._forma("C")
        self._hub_autenticado()

        response = self._formas_pagamento()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["formas_pagamento"]], [forma_a.pk, forma_b.pk, forma_c.pk])

    def test_formas_pagamento_evita_n_mais_um_em_cenario_controlado(self):
        self._hub_autenticado()
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="AV", descricao="À vista")
        for idx in range(3):
            forma = self._forma(f"{idx:03d}", prazo_pagamento=prazo)
            FormaPagamentoParcela.objects.create(forma=forma, ordem=1, dias=0, percentual=Decimal("1.000000"))

        with self.assertNumQueries(3):
            response = self._formas_pagamento()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["formas_pagamento"]), 3)


class SysvarHubTiposDespesaPdvApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Despesas Hub", documento="81222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Despesas Hub", documento="81222333000182")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Despesas", apelido_loja="DP", cnpj="81222333000181", estado="SP")
        self.outra_loja_mesma_empresa = Loja.objects.create(empresa=self.empresa, nome_loja="Outra Loja Despesas", apelido_loja="ODP", cnpj="81222333000183", estado="SP")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Despesas", apelido_loja="OOD", cnpj="81222333000182", estado="SP")

    def _hub_autenticado(self, loja=None, hub_uuid="dddddddd-dddd-4ddd-8ddd-dddddddddddd"):
        hub = SysvarHub.objects.create(loja=loja or self.loja, hub_uuid=hub_uuid, ativo=True)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _tipos_despesa_pdv(self, query=""):
        return self.client.get(f"/api/hub/tipos-despesa-pdv/{query}")

    def _natureza(self, codigo, empresa=None, **kwargs):
        defaults = {
            "empresa": empresa if empresa is not None else self.empresa,
            "codigo": codigo,
            "categoria_principal": "Administrativo",
            "subcategoria": "Loja",
            "descricao": f"Natureza {codigo}",
            "tipo": "DESPESA",
            "status": "ATIVO",
            "tipo_natureza": "DEBITO",
            "natureza_operacao": "DESPESA",
            "categoria_gerencial": "Operacional",
            "movimenta_financeiro": True,
            "entra_dre": True,
        }
        defaults.update(kwargs)
        return Nat_Lancamento.objects.create(**defaults)

    def _tipo_despesa(self, codigo, empresa=None, natureza=None, **kwargs):
        empresa = empresa if empresa is not None else self.empresa
        defaults = {
            "empresa": empresa,
            "codigo": codigo,
            "descricao": f"Despesa {codigo}",
            "Idnatureza": natureza or self._natureza(f"N{codigo}", empresa=empresa),
            "ativo": True,
            "exige_documento": False,
        }
        defaults.update(kwargs)
        return TipoDespesaPdv.objects.create(**defaults)

    def test_tipos_despesa_pdv_exige_autenticacao_hub(self):
        self.client.force_authenticate(user=None)
        self.client.credentials()

        response = self._tipos_despesa_pdv()

        self.assertIn(response.status_code, (401, 403))

    def test_snapshot_usa_escopo_do_hub_e_serializa_contrato(self):
        hub = self._hub_autenticado()
        natureza_lanche = self._natureza(
            "3301",
            descricao="Lanche",
            categoria_principal="Alimentação",
            subcategoria="Equipe",
            categoria_gerencial="Loja",
            movimenta_financeiro=True,
            entra_dre=True,
        )
        lanche = self._tipo_despesa("LAN", natureza=natureza_lanche, descricao="Lanche de loja")
        taxi = self._tipo_despesa("TAX", descricao="Taxi", exige_documento=True)
        self._tipo_despesa("INA", descricao="Inativa", ativo=False)
        self._tipo_despesa("OUT", empresa=self.outra_empresa, descricao="Outra Empresa")

        response = self._tipos_despesa_pdv(f"?loja_id={self.outra_loja_mesma_empresa.pk}&empresa_id={self.outra_empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["tipos_despesa_pdv_versao"], 1)
        self.assertIn("gerado_em", response.data)
        self.assertEqual(response.data["hub"], {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)})
        self.assertEqual(response.data["empresa"], {"id": self.empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja.pk})
        self.assertEqual([item["id"] for item in response.data["tipos_despesa_pdv"]], [lanche.pk, taxi.pk])

        item = response.data["tipos_despesa_pdv"][0]
        self.assertEqual(set(item.keys()), {"id", "codigo", "descricao", "exige_documento", "ativo", "natureza"})
        self.assertEqual(item["codigo"], "LAN")
        self.assertEqual(item["descricao"], "Lanche de loja")
        self.assertFalse(item["exige_documento"])
        self.assertTrue(item["ativo"])
        self.assertEqual(item["natureza"], {
            "id": natureza_lanche.pk,
            "codigo": "3301",
            "descricao": "Lanche",
            "categoria_principal": "Alimentação",
            "subcategoria": "Equipe",
            "tipo": "DESPESA",
            "status": "ATIVO",
            "tipo_natureza": "DEBITO",
            "natureza_operacao": "DESPESA",
            "categoria_gerencial": "Loja",
            "movimenta_financeiro": True,
            "entra_dre": True,
        })
        for termo in ["plano_contabil", "conta_contabil", "token", "password", "secret", "hash"]:
            self.assertNotIn(termo, str(response.data).lower())

    def test_hub_de_outra_empresa_nao_consegue_alterar_escopo_por_query_string(self):
        self._tipo_despesa("EMP1")
        tipo_outra = self._tipo_despesa("EMP2", empresa=self.outra_empresa)
        self._hub_autenticado(loja=self.loja_outra_empresa, hub_uuid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

        response = self._tipos_despesa_pdv(f"?loja_id={self.loja.pk}&empresa_id={self.empresa.pk}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["empresa"], {"id": self.outra_empresa.pk})
        self.assertEqual(response.data["loja"], {"id": self.loja_outra_empresa.pk})
        self.assertEqual([item["id"] for item in response.data["tipos_despesa_pdv"]], [tipo_outra.pk])

    def test_ordenacao_deterministica_por_descricao_codigo_e_id(self):
        tipo_b = self._tipo_despesa("B", descricao="Taxi")
        tipo_a = self._tipo_despesa("A", descricao="Taxi")
        tipo_c = self._tipo_despesa("C", descricao="Abastecimento")
        self._hub_autenticado()

        response = self._tipos_despesa_pdv()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["tipos_despesa_pdv"]], [tipo_c.pk, tipo_a.pk, tipo_b.pk])

    def test_tipos_despesa_pdv_evita_n_mais_um_em_cenario_controlado(self):
        self._hub_autenticado()
        for idx in range(3):
            self._tipo_despesa(f"{idx:03d}")

        with self.assertNumQueries(2):
            response = self._tipos_despesa_pdv()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["tipos_despesa_pdv"]), 3)


class SysvarHubSyncPushApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Sync Hub", documento="71222333000181")
        self.outra_empresa = Empresa.objects.create(nome="Outra Empresa Sync Hub", documento="81222333000181")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Sync", apelido_loja="SYNC", cnpj="71222333000181", estado="SP")
        self.outra_loja = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja Outra Sync", apelido_loja="OSYNC", cnpj="81222333000181", estado="SP")
        self.caixa = Caixa.objects.create(empresa=self.empresa, idloja=self.loja, tipo_caixa=Caixa.TIPO_LOJA, codigo="CX1", descricao="Caixa Loja")
        self.caixa_outra = Caixa.objects.create(empresa=self.outra_empresa, idloja=self.outra_loja, tipo_caixa=Caixa.TIPO_LOJA, codigo="CX2", descricao="Caixa Outra")
        self.cliente = Cliente.objects.create(empresa=self.empresa, tipo_pessoa="PF", documento="39053344705", cpf="39053344705", nome_cliente="Cliente Sync", apelido="Cliente")
        self.vendedor = Funcionarios.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            nomefuncionario="Vendedor Sync",
            cpf="52998224725",
            matricula="000001",
            participa_vendas=True,
            ativo=True,
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Codigo="UN", Descricao="UNIDADE")
        self.grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade Sync")
        self.cor = Cor.objects.create(empresa=self.empresa, Descricao="AZUL", Codigo="AZ", Cor="Azul")
        self.tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="40", Descricao="40")
        ConfigEan.objects.create(empresa=self.empresa, company_prefix="2234")
        self.produto = Produto.objects.create(
            empresa=self.empresa,
            referencia="26-01-01099",
            descricao="Produto Sync",
            unidade=self.unidade,
            ncm="6204.62.00",
            origem_mercadoria=0,
            cfop_venda_dentro="5102",
        )
        self.sku = ProdutoDetalhe.objects.create(produto=self.produto, idcor=self.cor, idtamanho=self.tamanho)
        self.estoque = Estoque.objects.create(CodigodeBarra=self.sku.ean13, referencia=self.produto.referencia, Idloja=self.loja, Estoque=Decimal("5.000"))

    def _hub_autenticado(self, ativo=True):
        hub = SysvarHub.objects.create(loja=self.loja, hub_uuid="11111111-2222-4333-8444-555555555555", ativo=ativo)
        token = hub.gerar_token()
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Hub {token}")
        return hub

    def _push(self, eventos):
        return self.client.post("/api/hub/sync/push/", {"versao": 1, "eventos": eventos}, format="json")

    def _evento(self, tipo, payload, evento_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", chave="chave-1"):
        return {
            "evento_uuid": evento_uuid,
            "chave_idempotencia": chave,
            "tipo": tipo,
            "ocorrido_em": timezone.now().isoformat(),
            "payload": payload,
        }

    def _payload_movimento(self, movimento_uuid="cccccccc-cccc-4ccc-8ccc-cccccccccccc", valor="10.00", caixa=None):
        payload = {"movimento_uuid": movimento_uuid, "tipo": "SANGRIA", "valor": valor}
        if caixa is not None:
            payload["caixa_retaguarda_id"] = caixa
        return payload

    def _payload_venda(self, venda_uuid="12121212-1212-4121-8121-121212121212", **extras):
        payload = {
            "venda_uuid": venda_uuid,
            "caixa_retaguarda_id": self.caixa.pk,
            "cliente_retaguarda_id": self.cliente.pk,
            "vendedor_retaguarda_id": self.vendedor.pk,
            "total": "10.00",
            "valor_recebido": "10.00",
            "itens": [{
                "produto_retaguarda_id": self.produto.pk,
                "sku_retaguarda_id": self.sku.pk,
                "ean": self.sku.ean13,
                "quantidade": 1,
                "preco_unitario": "10.00",
                "desconto": "0.00",
            }],
            "pagamentos": [{"codigo": "DINHEIRO", "descricao": "Dinheiro", "valor": "10.00"}],
        }
        payload.update(extras)
        return payload

    def test_sync_push_exige_hub_autenticado_e_ativo(self):
        response = self._push([])
        self.assertIn(response.status_code, (401, 403))

        self._hub_autenticado(ativo=False)
        response = self._push([])
        self.assertIn(response.status_code, (401, 403))

    def test_cliente_local_cria_mapeamento_e_retry_nao_duplica(self):
        self._hub_autenticado()
        payload = {
            "cliente_uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "tipo_pessoa": "PF",
            "documento": "12345678901",
            "nome": "Cliente Local",
            "apelido": "Local",
        }

        response = self._push([self._evento("CLIENTE_LOCAL", payload)])
        self.assertEqual(response.status_code, 200, response.data)
        resultado = response.data["resultados"][0]
        self.assertEqual(resultado["status"], HubEventoRecebido.STATUS_PROCESSADO)
        self.assertTrue(HubClienteMapeamento.objects.filter(cliente_uuid=payload["cliente_uuid"]).exists())
        cliente_id = resultado["mapeamento"]["cliente_retaguarda_id"]

        response = self._push([self._evento("CLIENTE_LOCAL", payload)])
        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_DUPLICADO)
        self.assertEqual(response.data["resultados"][0]["mapeamento"]["cliente_retaguarda_id"], cliente_id)
        self.assertEqual(Cliente.objects.filter(empresa=self.empresa, documento="12345678901").count(), 1)

    def test_mesma_chave_com_payload_diferente_retorna_conflito(self):
        self._hub_autenticado()
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento())
        self.assertEqual(self._push([evento]).data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)

        evento["payload"] = self._payload_movimento(valor="11.00")
        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_CONFLITO)

    def test_conflito_nao_altera_evento_processado_original(self):
        self._hub_autenticado()
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento(), chave="original")
        self.assertEqual(self._push([evento]).data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)

        evento["payload"] = self._payload_movimento(valor="12.00")
        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_CONFLITO)
        registro = HubEventoRecebido.objects.get(chave_idempotencia="original")
        self.assertEqual(registro.status, HubEventoRecebido.STATUS_PROCESSADO)
        self.assertEqual(registro.mensagem_erro, "")

    def test_mesmo_evento_uuid_com_chave_diferente_retorna_conflito(self):
        self._hub_autenticado()
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento(), chave="chave-original")
        self.assertEqual(self._push([evento]).data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)

        evento["chave_idempotencia"] = "chave-outra"
        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_CONFLITO)
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 1)

    def test_mesma_chave_com_evento_uuid_diferente_retorna_conflito(self):
        self._hub_autenticado()
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento(), chave="chave-original")
        self.assertEqual(self._push([evento]).data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)

        evento["evento_uuid"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_CONFLITO)
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 1)

    def test_evento_erro_com_mesmo_conteudo_e_reprocessado_e_pode_virar_processado(self):
        self._hub_autenticado()
        caixa_id = 999
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento(caixa=caixa_id), chave="retry-ok")
        response = self._push([evento])
        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)

        Caixa.objects.create(Idcaixa=caixa_id, empresa=self.empresa, idloja=self.loja, tipo_caixa=Caixa.TIPO_LOJA, codigo="CX999", descricao="Caixa Retry")
        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)
        registro = HubEventoRecebido.objects.get(chave_idempotencia="retry-ok")
        self.assertEqual(registro.status, HubEventoRecebido.STATUS_PROCESSADO)
        self.assertEqual(registro.mensagem_erro, "")
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 1)

    def test_evento_erro_com_mesmo_conteudo_falha_novamente_e_permanece_erro(self):
        self._hub_autenticado()
        evento = self._evento("MOVIMENTO_CAIXA", self._payload_movimento(caixa=998), chave="retry-fail")
        response = self._push([evento])
        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)
        primeira_mensagem = HubEventoRecebido.objects.get(chave_idempotencia="retry-fail").mensagem_erro

        response = self._push([evento])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)
        registro = HubEventoRecebido.objects.get(chave_idempotencia="retry-fail")
        self.assertEqual(registro.status, HubEventoRecebido.STATUS_ERRO)
        self.assertEqual(registro.mensagem_erro, primeira_mensagem)
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 0)

    def test_lote_processa_eventos_independentemente(self):
        self._hub_autenticado()
        eventos = [
            self._evento("MOVIMENTO_CAIXA", {"movimento_uuid": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "tipo": "SUPRIMENTO", "valor": "5.00"}, chave="ok-1"),
            self._evento("CLIENTE_LOCAL", {"cliente_uuid": "invalido"}, evento_uuid="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", chave="erro-1"),
            self._evento("SESSAO_CAIXA_FECHADA", {"sessao_uuid": "ffffffff-ffff-4fff-8fff-ffffffffffff", "valor_contado": "5.00"}, evento_uuid="99999999-9999-4999-8999-999999999999", chave="ok-2"),
        ]

        response = self._push(eventos)

        self.assertEqual([r["status"] for r in response.data["resultados"]], ["PROCESSADO", "ERRO", "PROCESSADO"])
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 1)
        self.assertEqual(HubSessaoCaixaRecebida.objects.count(), 1)

    def test_venda_finalizada_cria_venda_itens_pagamentos_estoque_e_retry_nao_rebaixa(self):
        self._hub_autenticado()
        payload = self._payload_venda(total="20.00", valor_recebido="20.00")
        payload["itens"][0]["quantidade"] = 2
        payload["pagamentos"][0]["valor"] = "20.00"

        response = self._push([self._evento("VENDA_FINALIZADA", payload)])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO)
        venda_id = response.data["resultados"][0]["mapeamento"]["venda_retaguarda_id"]
        self.assertTrue(HubVendaMapeamento.objects.filter(venda_id=venda_id).exists())
        self.assertEqual(VendaPdv.objects.get(pk=venda_id).total, Decimal("20.00"))
        self.assertEqual(VendaPdvItem.objects.filter(venda_id=venda_id).count(), 1)
        self.assertEqual(VendaPdvPagamento.objects.filter(venda_id=venda_id).count(), 1)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.Estoque, Decimal("3.000"))
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=VendaPdv.objects.get(pk=venda_id).documento).count(), 1)

        response = self._push([self._evento("VENDA_FINALIZADA", payload)])
        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_DUPLICADO)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.Estoque, Decimal("3.000"))

    def test_data_operacional_invalida_retorna_erro_e_nao_grava_fechamento_hoje(self):
        self._hub_autenticado()
        payload = {"fechamento_uuid": "18181818-1818-4181-8181-181818181818", "data_operacional": "data-invalida", "total_sistema": "7.00"}

        response = self._push([self._evento("FECHAMENTO_DIA", payload, chave="data-fechamento")])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)
        self.assertFalse(HubFechamentoDiaRecebido.objects.exists())

    def test_data_de_venda_informada_invalida_retorna_erro(self):
        self._hub_autenticado()
        payload = self._payload_venda(venda_uuid="19191919-1919-4191-8191-191919191919", finalizado_em="data-invalida")

        response = self._push([self._evento("VENDA_FINALIZADA", payload, chave="data-venda")])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)
        self.assertFalse(VendaPdv.objects.exists())

    def test_entidade_de_outra_empresa_bloqueada(self):
        self._hub_autenticado()
        payload = {
            "venda_uuid": "34343434-3434-4343-8343-343434343434",
            "caixa_retaguarda_id": self.caixa_outra.pk,
            "cliente_retaguarda_id": self.cliente.pk,
            "vendedor_retaguarda_id": self.vendedor.pk,
            "total": "1.00",
            "valor_recebido": "1.00",
            "itens": [],
            "pagamentos": [{"codigo": "DINHEIRO", "valor": "1.00"}],
        }

        response = self._push([self._evento("VENDA_FINALIZADA", payload)])

        self.assertEqual(response.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO)
        self.assertFalse(HubVendaMapeamento.objects.exists())

    def test_movimento_sessao_e_fechamento_dia_sao_persistidos_idempotentes(self):
        self._hub_autenticado()
        eventos = [
            self._evento("MOVIMENTO_CAIXA", {"movimento_uuid": "13131313-1313-4131-8131-131313131313", "tipo": "DESPESA", "valor": "7.00"}, chave="mov"),
            self._evento("SESSAO_CAIXA_FECHADA", {"sessao_uuid": "14141414-1414-4141-8141-141414141414", "valor_contado": "7.00"}, evento_uuid="15151515-1515-4151-8151-151515151515", chave="sessao"),
            self._evento("FECHAMENTO_DIA", {"fechamento_uuid": "16161616-1616-4161-8161-161616161616", "data_operacional": "2026-09-17", "total_sistema": "7.00"}, evento_uuid="17171717-1717-4171-8171-171717171717", chave="fech"),
        ]

        response = self._push(eventos)
        self.assertEqual([r["status"] for r in response.data["resultados"]], ["PROCESSADO", "PROCESSADO", "PROCESSADO"])
        self._push(eventos)

        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 1)
        self.assertEqual(HubSessaoCaixaRecebida.objects.count(), 1)
        self.assertEqual(HubFechamentoDiaRecebido.objects.count(), 1)

    def test_integrity_error_concorrente_resolve_para_registro_vencedor_sem_reprocessar(self):
        hub = self._hub_autenticado()
        payload = self._payload_movimento(movimento_uuid="20202020-2020-4020-8020-202020202020")
        evento = self._evento("MOVIMENTO_CAIXA", payload, evento_uuid="21212121-2121-4121-8121-212121212121", chave="concorrente")
        vencedor = HubEventoRecebido.objects.create(
            hub=hub,
            evento_uuid=evento["evento_uuid"],
            chave_idempotencia=evento["chave_idempotencia"],
            tipo=evento["tipo"],
            payload_hash=payload_hash(payload),
            payload=payload,
            status=HubEventoRecebido.STATUS_PROCESSADO,
            processado_em=timezone.now(),
        )
        vencedor.refresh_from_db()
        processor = HubSyncProcessor(hub)

        with patch.object(processor, "_buscar_evento_existente", side_effect=[None, vencedor]), patch("hub.sync.HubEventoRecebido.objects.create", side_effect=IntegrityError):
            resultado = processor.processar_evento(evento)

        self.assertEqual(resultado["status"], HubEventoRecebido.STATUS_DUPLICADO)
        self.assertEqual(HubMovimentoCaixaRecebido.objects.count(), 0)

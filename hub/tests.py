from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Empresa, Loja
from financeiro.models import Caixa
from hub.models import AtivacaoSysvarHub, SysvarHub
from produto.models import ConfigEan, Cor, Estoque, Grade, Produto, ProdutoDetalhe, Tabelapreco, TabelaprecoProduto, Tamanho, Unidade


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
        self.assertEqual(resp.data["catalogo_versao"], 1)
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
        self.assertNotIn("imagem", item)
        self.assertNotIn("imagem_url", item)

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

        with self.assertNumQueries(5):
            resp = self._catalogo()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["total_itens"], 3)

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Empresa, Loja
from hub.models import AtivacaoSysvarHub, SysvarHub


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

from unittest.mock import patch
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, SessaoUsuario, SessionToken, UserModulePermission
from accounts.services.sessions import token_hash
from auditoria.models import AuditAction, AuditLog
from cadastros.models import Cliente, Empresa, EmpresaContrato, Loja, ModuloSistema
from cadastros.services import ClientePadraoService


User = get_user_model()


class OperacionalBaseTest(TestCase):
    def setUp(self):
        self.operacional = ModuloSistema.objects.update_or_create(
            chave="operacional",
            defaults={"nome": "Operacional", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 1},
        )[0]
        self.cadastros = ModuloSistema.objects.update_or_create(
            chave="cadastros",
            defaults={"nome": "Cadastros", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 2},
        )[0]
        self.empresa = Empresa.objects.create(nome="Empresa Operacional", nome_fantasia="Operacional", documento="11222333000181", plano_completo=True)
        self.outra = Empresa.objects.create(nome="Outra Empresa", documento="22333444000102", plano_completo=True)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 1", apelido_loja="L1", cnpj="11222333000181")
        self.loja2 = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 2", apelido_loja="L2", cnpj="11222333000262")
        self.superuser = User.objects.create_superuser(username="root", password="senha12345", email="root@example.com")
        self.master = User.objects.create_user(username="master", password="senha12345", empresa=self.empresa, loja=self.loja)
        self.view_profile = PerfilAcesso.objects.create(empresa=self.empresa, nome="View")
        PerfilModuloPermissao.objects.create(perfil=self.view_profile, modulo=self.operacional, acesso=UserModulePermission.Access.VIEW)
        PerfilModuloPermissao.objects.create(perfil=self.view_profile, modulo=self.cadastros, acesso=UserModulePermission.Access.VIEW)
        self.edit_profile = PerfilAcesso.objects.create(empresa=self.empresa, nome="Edit")
        PerfilModuloPermissao.objects.create(perfil=self.edit_profile, modulo=self.operacional, acesso=UserModulePermission.Access.EDIT)
        PerfilModuloPermissao.objects.create(perfil=self.edit_profile, modulo=self.cadastros, acesso=UserModulePermission.Access.EDIT)
        self.user_view = User.objects.create_user(username="view", password="senha12345", empresa=self.empresa, loja=self.loja, perfil_principal=self.view_profile)
        self.user_view.lojas.set([self.loja])
        self.user_edit = User.objects.create_user(username="edit", password="senha12345", empresa=self.empresa, loja=self.loja, perfil_principal=self.edit_profile)
        self.user_edit.lojas.set([self.loja, self.loja2])
        self.user_none = User.objects.create_user(username="none", password="senha12345", empresa=self.empresa, loja=self.loja)
        EmpresaContrato.objects.update_or_create(empresa=self.empresa, defaults={"status": EmpresaContrato.STATUS_ATIVO, "usuario_master": self.master, "plano_completo": True, "limite_sessoes_simultaneas": 3})
        EmpresaContrato.objects.update_or_create(empresa=self.outra, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 3})
        self.client = APIClient()

    def active_session(self, user=None):
        raw = "raw-token-value"
        sessao = SessaoUsuario.objects.create(
            empresa=self.empresa,
            usuario=user or self.user_edit,
            loja=self.loja,
            token_key_hash=token_hash(raw),
            dispositivo_id="dev",
            ultima_atividade_em=timezone.now(),
        )
        SessionToken.objects.create(key_hash=token_hash(raw), session=sessao)
        return raw, sessao


class EmpresaSuspensaoTests(OperacionalBaseTest):
    def test_superusuario_suspende_encerra_sessoes_revoga_tokens_e_audita(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {
            "motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA,
            "observacao": "parcela em aberto",
            "confirmacao": self.empresa.nome,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        contrato = self.empresa.contrato
        contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(contrato.status, EmpresaContrato.STATUS_SUSPENSO)
        self.assertFalse(sessao.ativa)
        self.assertIsNotNone(sessao.session_token.revoked_at)
        log = AuditLog.objects.get(action=AuditAction.CONTRACT_SUSPENDED)
        self.assertEqual(log.metadata["sessoes_encerradas"], 1)
        self.assertEqual(log.status_code, 200)
        res_login = self.client.post("/api/accounts/auth/token/", {"username": "edit", "password": "senha12345", "device_id": "novo"}, format="json")
        self.assertEqual(res_login.status_code, 401)
        self.assertEqual(res_login.data["code"], "CONTRACT_SUSPENDED")

    def test_master_e_usuario_comum_nao_suspendem(self):
        for user in (self.master, self.user_edit):
            self.client.force_authenticate(user)
            res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
            self.assertEqual(res.status_code, 403)

    def test_confirmacao_invalida_bloqueia(self):
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": "errado"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.empresa.contrato.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)

    def test_falha_auditoria_obrigatoria_gera_rollback(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        with patch("cadastros.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
        self.empresa.contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)
        self.assertTrue(sessao.ativa)

    def test_reativacao_funciona_sem_reativar_sessoes_antigas(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/reativar/")
        self.assertEqual(res.status_code, 200)
        self.empresa.contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)
        self.assertFalse(sessao.ativa)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CONTRACT_REACTIVATED).exists())


class LojaOperacionalTests(OperacionalBaseTest):
    def test_permissoes_view_edit_none_e_escopo_loja(self):
        self.client.force_authenticate(self.user_view)
        self.assertEqual(self.client.get("/api/cadastros/lojas/").status_code, 200)
        self.assertEqual(self.client.patch(f"/api/cadastros/lojas/{self.loja.pk}/", {"apelido_loja": "X"}).status_code, 403)
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.patch(f"/api/cadastros/lojas/{self.loja.pk}/", {"apelido_loja": "X"}).status_code, 200)
        self.client.force_authenticate(self.user_none)
        self.assertEqual(self.client.get("/api/cadastros/lojas/").status_code, 403)

    def test_validacoes_de_loja(self):
        self.client.force_authenticate(self.superuser)
        res = self.client.post("/api/cadastros/lojas/", {"nome_loja": "Sem Empresa", "apelido_loja": "SE", "cnpj": "11222333000343"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/cadastros/lojas/", {"empresa": self.empresa.pk, "nome_loja": "Duplicada", "apelido_loja": "D", "cnpj": "11222333000181"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/cadastros/lojas/", {"empresa": self.empresa.pk, "nome_loja": "Matriz", "apelido_loja": "M", "cnpj": "11222333000343", "tipo_unidade": Loja.TIPO_MATRIZ}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Loja.objects.get(pk=res.data["id"]).Matriz, "SIM")

    def test_ciclo_de_vida_usuarios_indicadores_e_auditoria(self):
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.get("/api/cadastros/lojas/indicadores/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/cadastros/lojas/{self.loja.pk}/usuarios/").status_code, 200)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja.pk}/inativar/")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "STORE_DEACTIVATION_BLOCKED")
        self.loja2.usuarios.clear()
        self.loja2.usuarios_permitidos.clear()
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja2.pk}/encerrar/", {"data": "2026-08-05", "motivo": "fechamento"}, format="json")
        self.assertEqual(res.status_code, 200)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja2.pk}/reabrir/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.STORE_CLOSED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.STORE_REOPENED).exists())


class ClienteMultiempresaTests(OperacionalBaseTest):
    def test_modelo_normaliza_documento_e_cpf_por_tipo_de_pessoa(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Maria Cliente",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="529.982.247-25",
        )

        self.assertEqual(cliente.documento, "52998224725")
        self.assertEqual(cliente.cpf, "52998224725")

        empresa_cliente = Cliente.objects.create(
            empresa=self.outra,
            nome_cliente="Empresa Cliente",
            tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA,
            documento="11.222.333/0001-81",
        )

        self.assertEqual(empresa_cliente.documento, "11222333000181")
        self.assertEqual(empresa_cliente.cpf, "11222333000181")

    def test_api_bloqueia_documento_duplicado_na_mesma_empresa(self):
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Existente",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Duplicado",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": "52998224725",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("documento", res.data)

    def test_cliente_padrao_nao_pode_ser_inativado_ou_excluido(self):
        padrao = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Consumidor Final",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento=Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
            cliente_padrao=True,
        )
        self.client.force_authenticate(self.user_edit)

        res_inativar = self.client.post(f"/api/cadastros/clientes/{padrao.pk}/inativar/")
        res_delete = self.client.delete(f"/api/cadastros/clientes/{padrao.pk}/")

        self.assertEqual(res_inativar.status_code, 400)
        self.assertEqual(res_delete.status_code, 400)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_DELETE_DENIED).exists())

    def test_indicadores_refletem_carteira_de_clientes(self):
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Ativo PF",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Bloqueado PJ",
            tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA,
            documento="11222333000181",
            bloqueio=True,
            motivo_bloqueio="inadimplencia",
        )
        self.client.force_authenticate(self.user_view)

        res = self.client.get("/api/cadastros/clientes/indicadores/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total"], 2)
        self.assertEqual(res.data["ativos"], 2)
        self.assertEqual(res.data["bloqueados"], 1)

    def test_consentimento_preenche_data_sem_apagar_historico(self):
        self.client.force_authenticate(self.user_edit)
        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Cliente Consentimento",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": "52998224725",
                "aceita_email": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        cliente = Cliente.objects.get(pk=res.data["id"])
        self.assertIsNotNone(cliente.consentimento_em)
        consentimento_em = cliente.consentimento_em

        res = self.client.patch(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {"aceita_email": False, "aceita_whatsapp": False, "aceita_sms": False},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.consentimento_em, consentimento_em)

    def test_api_nao_permite_criar_cliente_padrao_manualmente(self):
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Consumidor Final Manual",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
                "cliente_padrao": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("cliente_padrao", res.data)
        self.assertFalse(Cliente.objects.filter(empresa=self.empresa, cliente_padrao=True).exists())

    def test_api_nao_permite_marcar_cliente_comum_como_padrao(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Comum",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.patch(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {"cliente_padrao": True},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        cliente.refresh_from_db()
        self.assertFalse(cliente.cliente_padrao)

    def test_servico_oficial_cria_cliente_padrao(self):
        cliente, created = ClientePadraoService.obter_ou_criar(self.empresa, aplicar=True)

        self.assertTrue(created)
        self.assertTrue(cliente.cliente_padrao)
        self.assertEqual(cliente.documento, Cliente.DOCUMENTO_CONSUMIDOR_FINAL)
        self.assertEqual(cliente.cpf, Cliente.DOCUMENTO_CONSUMIDOR_FINAL)

    def test_diagnosticar_clientes_padrao_bloqueia_apply_ambiguo(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Comum",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        Cliente.objects.filter(pk=cliente.pk).update(cliente_padrao=True)

        with self.assertRaises(CommandError):
            call_command("diagnosticar_clientes_padrao", "--empresa-id", str(self.empresa.pk), "--apply", stdout=StringIO())

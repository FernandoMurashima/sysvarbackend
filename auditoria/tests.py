from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission
from auditoria.models import AuditAction, AuditCategory, AuditLog, AuditResult, AuditSeverity
from auditoria.sanitizer import REDACTED, sanitize_audit_data
from auditoria.services import AuditService
from cadastros.models import Empresa, EmpresaContrato, Loja, ModuloSistema


User = get_user_model()


class AuditBaseTest(TestCase):
    def setUp(self):
        self.modulo = ModuloSistema.objects.update_or_create(
            chave="auditoria",
            defaults={"nome": "Auditoria", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 95},
        )[0]
        self.empresa_a = Empresa.objects.create(nome="Empresa A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B", documento="22222222000102", plano_completo=True)
        self.loja_a1 = Loja.objects.create(empresa=self.empresa_a, nome_loja="Loja A1", apelido_loja="A1", cnpj="11111111000191")
        self.loja_a2 = Loja.objects.create(empresa=self.empresa_a, nome_loja="Loja A2", apelido_loja="A2", cnpj="11111111000272")
        self.loja_b1 = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B1", apelido_loja="B1", cnpj="22222222000102")
        self.superuser = User.objects.create_superuser(username="root", password="x", email="root@example.com")
        self.master_a = User.objects.create_user(username="mastera", password="x", empresa=self.empresa_a, loja=self.loja_a1)
        self.master_b = User.objects.create_user(username="masterb", password="x", empresa=self.empresa_b, loja=self.loja_b1)
        EmpresaContrato.objects.update_or_create(empresa=self.empresa_a, defaults={"status": EmpresaContrato.STATUS_ATIVO, "usuario_master": self.master_a, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        EmpresaContrato.objects.update_or_create(empresa=self.empresa_b, defaults={"status": EmpresaContrato.STATUS_ATIVO, "usuario_master": self.master_b, "plano_completo": True, "limite_sessoes_simultaneas": 5})
        self.profile_a = PerfilAcesso.objects.create(empresa=self.empresa_a, nome="Auditor", ativo=True)
        PerfilModuloPermissao.objects.create(perfil=self.profile_a, modulo=self.modulo, acesso=UserModulePermission.Access.VIEW)
        self.user_a = User.objects.create_user(username="usera", password="x", empresa=self.empresa_a, loja=self.loja_a1, perfil_principal=self.profile_a)
        self.user_a.lojas.set([self.loja_a1])
        self.no_perm = User.objects.create_user(username="noperm", password="x", empresa=self.empresa_a, loja=self.loja_a1)

    def record(self, empresa=None, loja=None, user=None, **kwargs):
        return AuditService.success(
            kwargs.pop("action", AuditAction.OBJECT_UPDATED),
            category=kwargs.pop("category", AuditCategory.CADASTRO),
            empresa=empresa or self.empresa_a,
            loja=loja,
            user=user or self.user_a,
            app_label=kwargs.pop("app_label", "cadastros"),
            model=kwargs.pop("model", "empresa"),
            object_id=kwargs.pop("object_id", "1"),
            **kwargs,
        )


class AuditServiceTests(AuditBaseTest):
    def test_cria_evento_central_com_ids_e_snapshots(self):
        log = self.record(loja=self.loja_a1, before={"nome": "A"}, after={"nome": "B"})
        self.assertIsNotNone(log.event_id)
        self.assertEqual(log.empresa_id_snapshot, str(self.empresa_a.pk))
        self.assertEqual(log.empresa_nome_snapshot, "Empresa A")
        self.assertEqual(log.loja_nome_snapshot, "Loja A1")
        self.assertEqual(log.username_snapshot, "usera")
        self.assertEqual(log.changed_fields, ["nome"])

    def test_sanitizacao_simples_e_aninhada(self):
        data = sanitize_audit_data({"senha": "abc", "items": [{"access_token": "tok", "email": "teste@example.com"}]})
        self.assertEqual(data["senha"], REDACTED)
        self.assertEqual(data["items"][0]["access_token"], REDACTED)
        self.assertIn("***@", data["items"][0]["email"])

    def test_strings_longas_truncadas(self):
        log = self.record(metadata={"texto": "x" * 3000})
        self.assertTrue(log.metadata["texto"]["_truncated"])

    def test_on_commit_e_rollback(self):
        with self.captureOnCommitCallbacks(execute=True):
            AuditService.on_commit(action=AuditAction.CONTRACT_UPDATED, category=AuditCategory.CONTRACT, empresa=self.empresa_a, user=self.superuser)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CONTRACT_UPDATED).exists())
        try:
            with transaction.atomic():
                AuditService.on_commit(action=AuditAction.CONTRACT_STATUS_CHANGED, category=AuditCategory.CONTRACT, empresa=self.empresa_a, user=self.superuser)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        self.assertFalse(AuditLog.objects.filter(action=AuditAction.CONTRACT_STATUS_CHANGED).exists())

    def test_immutabilidade_model_manager_api(self):
        log = self.record()
        log.action = AuditAction.USER_LOGIN
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=log.pk).update(action=AuditAction.USER_LOGIN)
        with self.assertRaises(ValidationError):
            AuditLog.objects.create(action=AuditAction.USER_LOGIN, app_label="x", model="y")

    def test_audit_required_bloqueia_operacao(self):
        with self.assertRaises(ValidationError):
            AuditService.record(action=AuditAction.USER_LOGIN, category="INVALID", audit_required=True)


class AuditApiTests(AuditBaseTest):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.log_a1 = self.record(loja=self.loja_a1, object_id="A1")
        self.log_a2 = self.record(loja=self.loja_a2, object_id="A2")
        self.log_b1 = self.record(empresa=self.empresa_b, loja=self.loja_b1, user=self.master_b, object_id="B1")

    def test_superusuario_ve_todas_empresas(self):
        self.client.force_authenticate(self.superuser)
        res = self.client.get("/api/auditoria/logs/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 3)

    def test_master_ve_apenas_propria_empresa(self):
        self.client.force_authenticate(self.master_a)
        res = self.client.get("/api/auditoria/logs/")
        self.assertEqual(res.status_code, 200)
        ids = {row["object_id"] for row in res.data["results"]}
        self.assertEqual(ids, {"A1", "A2"})

    def test_usuario_comum_respeita_lojas(self):
        self.client.force_authenticate(self.user_a)
        res = self.client.get("/api/auditoria/logs/")
        self.assertEqual(res.status_code, 200)
        ids = {row["object_id"] for row in res.data["results"]}
        self.assertEqual(ids, {"A1"})

    def test_sem_permissao_recebe_403_e_registra_negado(self):
        self.client.force_authenticate(self.no_perm)
        res = self.client.get("/api/auditoria/logs/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.AUDIT_ACCESS_DENIED, result=AuditResult.DENIED).exists())

    def test_filtro_empresa_nao_faz_bypass(self):
        self.client.force_authenticate(self.master_a)
        res = self.client.get(f"/api/auditoria/logs/?empresa={self.empresa_b.pk}")
        self.assertEqual(res.status_code, 200)
        ids = {row["object_id"] for row in res.data["results"]}
        self.assertTrue({"A1", "A2"}.issubset(ids))
        self.assertNotIn("B1", ids)

    def test_endpoint_sem_escrita(self):
        self.client.force_authenticate(self.superuser)
        self.assertEqual(self.client.post("/api/auditoria/logs/", {}).status_code, 405)
        self.assertEqual(self.client.patch(f"/api/auditoria/logs/{self.log_a1.pk}/", {}).status_code, 405)
        self.assertEqual(self.client.delete(f"/api/auditoria/logs/{self.log_a1.pk}/").status_code, 405)

    def test_indicadores_respeitam_filtros(self):
        AuditService.denied(AuditAction.PERMISSION_DENIED, category=AuditCategory.ACCESS, empresa=self.empresa_a, loja=self.loja_a1, user=self.user_a, app_label="accounts", model="user")
        self.client.force_authenticate(self.user_a)
        res = self.client.get("/api/auditoria/logs/indicadores/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total"], 2)
        self.assertEqual(res.data["denied"], 1)

    def test_exportacao_respeita_empresa_e_gera_auditoria(self):
        self.client.force_authenticate(self.master_a)
        res = self.client.get("/api/auditoria/logs/exportar/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")
        self.assertIn("A1", content)
        self.assertNotIn("B1", content)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.AUDIT_EXPORT).exists())

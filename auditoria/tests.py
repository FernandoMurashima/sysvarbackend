from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission
from auditoria.models import AuditAction, AuditCategory, AuditLog, AuditResult, AuditSeverity
from auditoria.sanitizer import REDACTED, sanitize_audit_data
from auditoria.services import AuditService
from accounts.services.effective_access import audit_event
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
        self.profile_edit = PerfilAcesso.objects.create(empresa=self.empresa_a, nome="Auditor Edit", ativo=True)
        PerfilModuloPermissao.objects.create(perfil=self.profile_edit, modulo=self.modulo, acesso=UserModulePermission.Access.EDIT)
        self.user_a = User.objects.create_user(username="usera", password="x", empresa=self.empresa_a, loja=self.loja_a1, perfil_principal=self.profile_a)
        self.user_a.lojas.set([self.loja_a1])
        self.user_edit = User.objects.create_user(username="editora", password="x", empresa=self.empresa_a, loja=self.loja_a1, perfil_principal=self.profile_edit)
        self.user_edit.lojas.set([self.loja_a1, self.loja_a2])
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

    def test_snapshot_usa_campos_reais(self):
        self.empresa_a.nome_fantasia = "Fantasia A"
        self.empresa_a.save(update_fields=["nome_fantasia"])
        self.loja_a1.apelido_loja = "Apelido A1"
        self.loja_a1.save(update_fields=["apelido_loja"])
        log = self.record(empresa=self.empresa_a, loja=self.loja_a1)
        self.assertEqual(log.empresa_nome_snapshot, "Fantasia A")
        self.assertEqual(log.loja_nome_snapshot, "Loja A1")

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
        with self.assertRaises(ValidationError):
            AuditLog.objects.bulk_create([AuditLog(action=AuditAction.USER_LOGIN, app_label="x", model="y")])
        with self.assertRaises(ValidationError):
            AuditLog.objects.bulk_update([log], ["action"])
        with self.assertRaises(ValidationError):
            AuditLog.objects.update_or_create(app_label="x", model="y", defaults={"action": AuditAction.USER_LOGIN})
        with self.assertRaises(ValidationError):
            AuditLog.objects.get_or_create(app_label="x", model="y", defaults={"action": AuditAction.USER_LOGIN})
        self.assertIsNotNone(AuditService.success(AuditAction.USER_LOGIN, category=AuditCategory.SECURITY, user=self.user_a, app_label="accounts", model="session"))

    def test_audit_required_bloqueia_operacao(self):
        with self.assertRaises(ValidationError):
            AuditService.record(action=AuditAction.USER_LOGIN, category="INVALID", audit_required=True)

    def test_catalogo_de_acoes_e_wrapper_legado(self):
        self.assertIsNotNone(self.record(action=AuditAction.OBJECT_UPDATED))
        self.assertEqual(AuditService.success("create", category=AuditCategory.CADASTRO, empresa=self.empresa_a, app_label="x", model="y").action, AuditAction.OBJECT_CREATED)
        with self.assertRaises(ValidationError):
            AuditService.record(action="ACAO_SOLTA", category=AuditCategory.SYSTEM, audit_required=True)
        audit_event("acao_antiga_sem_catalogo", user=self.user_a, changes={"x": "y"})
        log = AuditLog.objects.filter(action=AuditAction.LEGACY_EVENT).latest("id")
        self.assertEqual(log.metadata["legacy_action"], "acao_antiga_sem_catalogo")

    def test_required_rollback_e_on_commit_falho(self):
        try:
            with transaction.atomic():
                Empresa.objects.create(nome="Rollback Ltda", documento="33333333000113")
                AuditService.record(action="INVALIDA", category=AuditCategory.SYSTEM, audit_required=True)
        except ValidationError:
            pass
        self.assertFalse(Empresa.objects.filter(nome="Rollback Ltda").exists())
        before = AuditService.failure_count
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                empresa = Empresa.objects.create(nome="Commit Ltda", documento="44444444000104")
                AuditService.on_commit(action="INVALIDA", category=AuditCategory.SYSTEM, empresa=empresa, app_label="x", model="y")
        self.assertTrue(Empresa.objects.filter(nome="Commit Ltda").exists())
        self.assertGreater(AuditService.failure_count, before)

    def test_migration_historica_preenche_contexto_sem_inventar(self):
        from importlib import import_module

        migration = import_module("auditoria.migrations.0005_backfill_historical_context")
        log = AuditLog.objects.internal_create(action=AuditAction.OBJECT_UPDATED, app_label="cadastros", model="empresa", object_id=self.empresa_a.pk, user=self.user_a)
        orphan = AuditLog.objects.internal_create(action=AuditAction.OBJECT_UPDATED, app_label="x", model="y", object_id="999")
        filled = AuditLog.objects.internal_create(action=AuditAction.OBJECT_UPDATED, app_label="cadastros", model="empresa", object_id=self.empresa_a.pk, empresa=self.empresa_b, empresa_id_snapshot=str(self.empresa_b.pk), empresa_nome_snapshot="Preenchida")
        migration.backfill_historical_context(apps, None)
        log.refresh_from_db()
        orphan.refresh_from_db()
        filled.refresh_from_db()
        self.assertEqual(log.empresa_id, self.empresa_a.pk)
        self.assertEqual(log.empresa_id_snapshot, str(self.empresa_a.pk))
        self.assertEqual(log.username_snapshot, "usera")
        self.assertIsNone(orphan.empresa_id)
        self.assertEqual(filled.empresa_id_snapshot, str(self.empresa_b.pk))
        self.assertEqual(filled.empresa_nome_snapshot, "Preenchida")


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

    def test_usuario_view_acessa_e_edit_exporta(self):
        self.client.force_authenticate(self.user_a)
        self.assertEqual(self.client.get("/api/auditoria/logs/").status_code, 200)
        self.assertEqual(self.client.get("/api/auditoria/logs/exportar/").status_code, 403)
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.get("/api/auditoria/logs/").status_code, 200)
        self.assertEqual(self.client.get("/api/auditoria/logs/exportar/").status_code, 200)

    def test_sem_permissao_recebe_403_e_registra_negado(self):
        self.client.force_authenticate(self.no_perm)
        res = self.client.get("/api/auditoria/logs/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.AUDIT_ACCESS_DENIED, result=AuditResult.DENIED).exists())

    def test_filtro_empresa_nao_faz_bypass(self):
        self.client.force_authenticate(self.master_a)
        res = self.client.get(f"/api/auditoria/logs/?empresa={self.empresa_b.pk}")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.AUDIT_ACCESS_DENIED, result=AuditResult.DENIED).count(), 1)

    def test_filtro_loja_nao_permitida_retorna_403_sem_recursao(self):
        self.client.force_authenticate(self.user_a)
        res = self.client.get(f"/api/auditoria/logs/?loja={self.loja_a2.pk}")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.AUDIT_ACCESS_DENIED, result=AuditResult.DENIED, status_code=403).count(), 1)

    def test_master_consulta_lojas_da_empresa_mas_nao_outra_empresa(self):
        self.client.force_authenticate(self.master_a)
        self.assertEqual(self.client.get(f"/api/auditoria/logs/?loja={self.loja_a2.pk}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/auditoria/logs/?loja={self.loja_b1.pk}").status_code, 403)

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
        log = AuditLog.objects.get(action=AuditAction.AUDIT_EXPORT)
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.metadata["format"], "CSV")
        self.assertEqual(log.metadata["exported_count"], 2)
        self.assertEqual(log.metadata["limit"], 5000)
        self.assertFalse(log.metadata["limit_reached"])

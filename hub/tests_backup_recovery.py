import hashlib
import json
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from auditoria.models import AuditAction, AuditLog
from hub import tests_backup_integrity as fixtures
from hub.backup_files import backup_lock, destination
from hub.backup_recovery import RecoveryError, SALT
from hub.models import HubBackup


class BackupRecoveryTests(TestCase):
    # Reuse the encrypted, private fixtures, not their inherited test suite.
    setUpTestData = classmethod(fixtures.BackupIntegrityTests.setUpTestData.__func__)
    cleanup_files = fixtures.BackupIntegrityTests.cleanup_files
    client_for = fixtures.BackupIntegrityTests.client_for
    full_manifest = fixtures.BackupIntegrityTests.full_manifest
    raw = fixtures.BackupIntegrityTests.raw
    start = fixtures.BackupIntegrityTests.start
    part = fixtures.BackupIntegrityTests.part
    all_parts = fixtures.BackupIntegrityTests.all_parts
    finish = fixtures.BackupIntegrityTests.finish
    full_received = fixtures.BackupIntegrityTests.full_received
    full = fixtures.BackupIntegrityTests.full
    incremental = fixtures.BackupIntegrityTests.incremental
    audit = fixtures.BackupIntegrityTests.audit
    corrupt = fixtures.BackupIntegrityTests.corrupt

    def setUp(self):
        fixtures.BackupIntegrityTests.setUp(self)
        self.recovery_settings = override_settings(HUB_BACKUP_RECOVERY_ATIVO=True,
            HUB_BACKUP_RECOVERY_AUTH_SECONDS=900, SECRET_KEY="fictitious-recovery-test-signing-key-only-1234567890")
        self.recovery_settings.enable(); self.addCleanup(self.recovery_settings.disable)
        self.support = get_user_model().objects.create_superuser(username="recovery-support", password=None)
        self.admin = APIClient(); self.admin.force_authenticate(self.support)

    def url(self, name, loja=None, empresa=None, **kwargs):
        return reverse("hub-backup-recovery-"+name, kwargs=dict(empresa_id=(empresa or self.empresa).pk,
            loja_id=(loja or self.loja).pk, **kwargs))

    def grant(self, point, **changes):
        payload = dict(ponto_uuid=str(point.backup_uuid), instalacao_uuid=str(point.instalacao_uuid),
                       servidor_mysql_uuid=str(point.servidor_mysql_uuid), autorizar=True, motivo="teste ficticio suporte")
        payload.update(changes)
        return self.admin.post(self.url("authorize"), payload, format="json", secure=True)

    def download(self, point, grant, **extra):
        return self.admin.get(self.url("file", backup_uuid=point.backup_uuid), secure=True,
                              HTTP_X_BACKUP_RECOVERY_TOKEN=grant["autorizacao_token"], **extra)

    def test_valid_point_lists_situation_and_requires_fresh_verification(self):
        full = self.full()
        before = self.admin.get(self.url("points"), secure=True)
        self.assertFalse(before.data["pontos"][0]["selecionavel"])
        self.assertEqual(before.data["pontos"][0]["estado_validacao"], "PENDENTE")
        self.audit(registrar=True)
        result = self.admin.get(self.url("points"), secure=True)
        self.assertEqual(result.status_code, 200)
        point = result.data["pontos"][0]
        self.assertTrue(point["selecionavel"])
        self.assertTrue(point["revalidacao_obrigatoria"])
        self.assertEqual(point["ponto_uuid"], str(full.backup_uuid))
        self.assertIn("gerado_em", point)
        self.assertFalse(result.data["restauracao_homologada"])

    def test_package_order_manifest_hash_coordinates_and_original_files(self):
        full = self.full(); one = self.incremental(full); two = self.incremental(one)
        self.audit(registrar=True)
        originals = {b.pk: destination(b).read_bytes() for b in (full,one,two)}
        result = self.grant(two)
        self.assertEqual(result.status_code, 201, result.data)
        manifest = result.data["manifesto"]
        files = manifest["arquivos"]
        self.assertEqual([f["backup_uuid"] for f in files], [str(b.backup_uuid) for b in (full,one,two)])
        self.assertEqual([f["ordem"] for f in files], [0,1,2])
        self.assertEqual([f["sequencia"] for f in files], [0,1,2])
        self.assertEqual(files[1]["inicio"]["anchor"], files[0]["inicio"]["anchor"])
        self.assertEqual(files[2]["inicio"]["anchor"], files[1]["fim"]["anchor"])
        self.assertEqual(hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",",":"), ensure_ascii=True).encode()).hexdigest(), result.data["manifesto_sha256"])
        for backup in (full,one,two):
            response = self.download(backup, result.data)
            self.assertEqual(response.status_code, 200)
            data = b"".join(response.streaming_content); response.close()
            self.assertEqual(data, self.payload)
            self.assertEqual(hashlib.sha256(data).hexdigest(), response["X-Backup-SHA256"])
            self.assertEqual(len(data), int(response["Content-Length"]))
            self.assertEqual(destination(backup).read_bytes(), originals[backup.pk])
        self.assertFalse(manifest["restauracao_homologada"])

    def test_received_pending_is_never_treated_as_validated(self):
        point = self.full()
        result = self.grant(point)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data["code"], "VALIDATED_CHAIN_REQUIRED")
        self.assertFalse(AuditLog.objects.filter(action=AuditAction.HUB_BACKUP_RECOVERY_AUTHORIZED).exists())

    def test_explicit_support_confirmation_and_reason_required(self):
        point = self.full(); self.audit(registrar=True)
        for changes in ({"autorizar":False},{"autorizar":1},{"motivo":""},{"motivo":" "}):
            self.assertEqual(self.grant(point, **changes).status_code, 400)

    def test_corruption_after_previous_validation_rejects_authorization(self):
        point = self.full(); self.audit(registrar=True)
        self.corrupt(point)
        self.assertEqual(self.grant(point).status_code, 409)
        point.refresh_from_db()
        self.assertEqual(point.estado_validacao, "VALIDO")  # No silent mutation of historical state.

    def test_corruption_after_authorization_blocks_download_before_headers(self):
        point = self.full(); self.audit(registrar=True)
        grant = self.grant(point).data
        self.corrupt(point)
        result = self.download(point, grant)
        self.assertEqual(result.status_code, 409)
        self.assertFalse(getattr(result, "streaming", False))

    def test_missing_ancestor_file_and_incomplete_chain_rejected(self):
        full = self.full(); point = self.incremental(full); self.audit(registrar=True)
        source = destination(full); preserved = source.with_name("preserved-fictitious-file.bin")
        source.rename(preserved)
        self.assertEqual(self.grant(point).status_code, 503)
        self.assertTrue(preserved.exists())

    def test_later_corrupt_incremental_does_not_invalidate_selected_earlier_point(self):
        full = self.full(); earlier = self.incremental(full); later = self.incremental(earlier)
        self.audit(registrar=True); self.corrupt(later)
        grant = self.grant(earlier)
        self.assertEqual(grant.status_code, 201, grant.data)
        self.assertEqual(len(grant.data["manifesto"]["arquivos"]), 2)
        self.assertEqual(self.download(later, grant.data).status_code, 404)

    def test_different_installation_or_mysql_server_rejected(self):
        point = self.full(); self.audit(registrar=True)
        for field in ("instalacao_uuid", "servidor_mysql_uuid"):
            self.assertEqual(self.grant(point, **{field:str(uuid4())}).status_code, 404)
        data = self.admin.get(self.url("points"), {"instalacao_uuid":str(uuid4())}, secure=True)
        self.assertEqual(data.data["pontos"], [])

    def test_permission_denied_for_regular_user_and_admin_without_backup_permission(self):
        point = self.full(); self.audit(registrar=True)
        for role in ("Regular", "Admin"):
            user = get_user_model().objects.create_user(username="restricted-"+role, empresa=self.empresa, type=role)
            self.admin.force_authenticate(user)
            self.assertEqual(self.grant(point).status_code, 403)
            self.assertEqual(self.admin.get(self.url("points"), secure=True).status_code, 403)

    def test_existing_company_admin_permission_allows_only_own_company(self):
        point = self.full(); self.audit(registrar=True)
        user = get_user_model().objects.create_user(username="authorized-admin", empresa=self.empresa, type="Admin")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="hub", codename__in=["view_hubbackup","change_hubbackup"]))
        self.admin.force_authenticate(user)
        self.assertEqual(self.grant(point).status_code, 201)
        self.assertEqual(self.admin.get(self.url("points",loja=self.loja_b,empresa=self.empresa_b), secure=True).status_code, 403)

    def test_wrong_store_company_or_user_cannot_reuse_grant(self):
        point = self.full(); self.audit(registrar=True)
        grant = self.grant(point).data
        result = self.admin.get(self.url("file",loja=self.loja_b,empresa=self.empresa_b,backup_uuid=point.backup_uuid),
            secure=True,HTTP_X_BACKUP_RECOVERY_TOKEN=grant["autorizacao_token"])
        self.assertEqual(result.status_code, 403)
        other = get_user_model().objects.create_superuser(username="different-support", password=None)
        self.admin.force_authenticate(other)
        self.assertEqual(self.download(point, grant).status_code, 403)

    def test_anonymous_hub_token_and_invalid_admin_token_are_denied(self):
        anonymous = APIClient()
        for authorization in (None,"Hub FICTITIOUS-HUB-TOKEN","Token INVALID-FICTITIOUS"):
            if authorization:
                anonymous.credentials(HTTP_AUTHORIZATION=authorization)
            self.assertEqual(anonymous.get(self.url("points"), secure=True).status_code, 401)

    def test_existing_administrative_authentication_is_used(self):
        from types import SimpleNamespace
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION="Token FICTITIOUS-SESSION-TOKEN")
        with patch("accounts.authentication.ConcurrentSessionService.validate_session_token",
                   return_value=(self.support, SimpleNamespace(), SimpleNamespace())) as validate:
            result = client.get(self.url("points"), secure=True)
        self.assertEqual(result.status_code, 200)
        validate.assert_called_once_with("FICTITIOUS-SESSION-TOKEN")

    def test_https_required_and_recovery_disabled_by_default(self):
        self.assertEqual(self.admin.get(self.url("points")).status_code, 403)
        with override_settings(HUB_BACKUP_RECOVERY_ATIVO=False):
            self.assertEqual(self.admin.get(self.url("points"), secure=True).status_code, 503)

    def test_missing_historical_keys_fail_closed(self):
        point = self.full(); self.audit(registrar=True)
        grant = self.grant(point).data
        with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=""):
            self.assertEqual(self.grant(point).status_code, 503)
            self.assertEqual(self.download(point, grant).status_code, 503)

    def test_grant_tampering_and_expiry_are_rejected(self):
        point = self.full(); self.audit(registrar=True)
        grant = self.grant(point).data
        tampered = dict(grant,autorizacao_token=grant["autorizacao_token"]+"X")
        self.assertEqual(self.download(point,tampered).status_code,403)
        claims = signing.loads(grant["autorizacao_token"],salt=SALT)
        with patch("django.core.signing.time.time", return_value=1):
            old = signing.dumps(claims,salt=SALT)
        self.assertEqual(self.download(point,dict(grant,autorizacao_token=old)).status_code,403)

    def test_interruption_releases_locks_and_retry_preserves_originals(self):
        point = self.full(); self.audit(registrar=True)
        original = destination(point).read_bytes()
        grant = self.grant(point).data
        response = self.download(point,grant)
        first = next(iter(response.streaming_content))
        self.assertTrue(first)
        response.close()
        with backup_lock(point):
            pass
        self.assertEqual(destination(point).read_bytes(),original)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.HUB_BACKUP_RECOVERY_INTERRUPTED).exists())
        retry = self.download(point,grant)
        self.assertEqual(b"".join(retry.streaming_content),self.payload);retry.close()

    def test_response_closed_without_iteration_releases_preopened_resources(self):
        point = self.full();self.audit(registrar=True)
        grant = self.grant(point).data
        response = self.download(point,grant);response.close()
        with backup_lock(point):
            pass

    def test_range_rejected_and_no_cache_on_success_or_errors(self):
        point = self.full();self.audit(registrar=True)
        grant = self.grant(point).data
        rejected = self.download(point,grant,HTTP_RANGE="bytes=0-9")
        self.assertEqual(rejected.status_code,416)
        self.assertIn("no-store",rejected["Cache-Control"])
        response = self.download(point,grant)
        self.assertIn("no-store",response["Cache-Control"])
        self.assertEqual(response["X-Accel-Buffering"],"no")
        self.assertIn("X-Backup-Recovery-Token",response["Vary"])
        response.close()

    def test_revoked_permissions_block_previously_issued_authorization(self):
        point = self.full();self.audit(registrar=True)
        user = get_user_model().objects.create_user(username="revoked-support", empresa=self.empresa, type="Admin")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="hub", codename__in=["view_hubbackup","change_hubbackup"]))
        self.admin.force_authenticate(user)
        grant = self.grant(point).data
        user.user_permissions.clear()
        self.assertEqual(self.download(point,grant).status_code,403)

    def test_authorization_requires_durable_audit_without_token_or_sql_logs(self):
        point = self.full();self.audit(registrar=True)
        with patch("hub.backup_recovery.AuditService.required_success",return_value=None):
            self.assertEqual(self.grant(point).status_code,503)
        result = self.grant(point)
        log = AuditLog.objects.filter(action=AuditAction.HUB_BACKUP_RECOVERY_AUTHORIZED).last()
        rendered = json.dumps(log.metadata)
        self.assertNotIn(result.data["autorizacao_token"],rendered)
        self.assertNotIn(self.keys_json,rendered)
        self.assertNotIn("teste ficticio suporte",rendered)

    def test_independent_receiving_toggle_and_no_cleartext_temporary_files(self):
        point = self.full();self.audit(registrar=True)
        originals = {path for path in self.root.rglob("*") if path.is_file()}
        with override_settings(HUB_BACKUP_RECEBIMENTO_ATIVO=False):
            grant = self.grant(point)
            self.assertEqual(grant.status_code,201,grant.data)
            response = self.download(point,grant.data)
            self.assertEqual(b"".join(response.streaming_content),self.payload);response.close()
        self.assertEqual(originals,{path for path in self.root.rglob("*") if path.is_file()})


    def test_lost_or_revoked_hub_token_is_not_required_for_recovery(self):
        from hub.models import SysvarHub
        point = self.full();self.audit(registrar=True)
        SysvarHub.objects.filter(pk=self.hub.pk).update(ativo=False,token_hash=None)
        grant = self.grant(point)
        self.assertEqual(grant.status_code,201,grant.data)
        response = self.download(point,grant.data)
        self.assertEqual(b"".join(response.streaming_content),self.payload);response.close()

    def test_inactive_administrator_cannot_reuse_grant(self):
        point = self.full();self.audit(registrar=True)
        grant = self.grant(point).data
        get_user_model().objects.filter(pk=self.support.pk).update(is_active=False)
        self.assertEqual(self.download(point,grant).status_code,403)

    def test_corruption_during_stream_withholds_final_part_and_releases_lock(self):
        point = self.full();self.audit(registrar=True)
        grant = self.grant(point).data
        response = self.download(point,grant)
        self.corrupt(point)  # Simulate out-of-band mutation only in the fictitious file.
        received = bytearray()
        with self.assertRaises(RecoveryError):
            for block in response.streaming_content:
                received.extend(block)
        response.close()
        self.assertLess(len(received),len(self.payload))
        with backup_lock(point):
            pass

    def test_bound_superuser_company_scope_is_preserved(self):
        get_user_model().objects.filter(pk=self.support.pk).update(empresa=self.empresa)
        result = self.admin.get(self.url("points",loja=self.loja_b,empresa=self.empresa_b),secure=True)
        self.assertEqual(result.status_code,403)

    def test_director_requires_store_scope_in_addition_to_backup_permissions(self):
        point = self.full();self.audit(registrar=True)
        user = get_user_model().objects.create_user(username="limited-director",empresa=self.empresa,type="Diretor")
        # Creation assigns the existing full-company default profile. Explicitly
        # construct a store-limited fixture instead of changing that business rule.
        get_user_model().objects.filter(pk=user.pk).update(perfil_principal=None)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="hub",codename__in=["view_hubbackup","change_hubbackup"]))
        self.admin.force_authenticate(user)
        self.assertEqual(self.grant(point).status_code,404)
        user.lojas.add(self.loja)
        self.assertEqual(self.grant(point).status_code,201)

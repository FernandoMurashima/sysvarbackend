import hashlib
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings

from hub import tests_backup_recebimento as fixtures
from hub.backup_files import backup_lock, destination
from hub.backup_integrity import auditar_backups_hub, IntegrityError
from hub.models import HubBackup


class BackupIntegrityTests(TestCase):
    # Reuse the existing private storage, encrypted uploads and fictitious fixtures;
    # do not inherit its tests or rerun the whole receiving suite.
    setUpTestData = classmethod(fixtures.BackupRecebimentoTests.setUpTestData.__func__)
    cleanup_files = fixtures.BackupRecebimentoTests.cleanup_files
    client_for = fixtures.BackupRecebimentoTests.client_for
    full_manifest = fixtures.BackupRecebimentoTests.full_manifest
    raw = fixtures.BackupRecebimentoTests.raw
    start = fixtures.BackupRecebimentoTests.start
    part = fixtures.BackupRecebimentoTests.part
    all_parts = fixtures.BackupRecebimentoTests.all_parts
    finish = fixtures.BackupRecebimentoTests.finish
    full_received = fixtures.BackupRecebimentoTests.full_received

    def setUp(self):
        fixtures.BackupRecebimentoTests.setUp(self)
        tz = override_settings(HUB_BACKUP_RETENTION_TIME_ZONE="America/Sao_Paulo")
        tz.enable(); self.addCleanup(tz.disable)
        self.mysql_uuid = str(uuid4())
        self.now = datetime(2026, 10, 15, 12, tzinfo=dt_timezone.utc)
        self.documents = {}

    def full(self, when=None, receive=True):
        doc = self.full_manifest(self.payload)
        doc["mysql"]["server_uuid"] = self.mysql_uuid
        when = when or self.now - timedelta(days=10)
        doc.update(started_at_utc=(when - timedelta(seconds=10)).isoformat(), completed_at_utc=when.isoformat())
        self.manifest = doc
        self.documents[doc["backup_id"]] = doc
        state = self.full_received() if receive else self.start().data
        return HubBackup.objects.get(backup_uuid=state["backup_uuid"])

    def incremental(self, previous, when=None):
        root = previous if previous.tipo == "FULL" else previous.full_referencia
        root_doc = self.documents[root.backup_uuid.hex]
        prior_doc = self.documents[previous.backup_uuid.hex]
        self.manifest = root_doc
        doc = fixtures.BackupRecebimentoTests.incremental(self, {"backup_uuid": str(root.backup_uuid)}, self.payload)
        point = prior_doc["binlog"] if previous.tipo == "FULL" else prior_doc["end"]
        end = dict(algorithm="MYSQL8_CRC32_PREFIX_V1", file=point["file"], position=point["position"]+100,
                   sha256=hashlib.sha256(uuid4().bytes).hexdigest())
        when = when or self.now
        doc.update(sequence=previous.sequencia+1, previous_id=str(previous.backup_uuid),
                   previous_manifest_sha256=previous.manifesto_sha256,
                   started_at_utc=(when-timedelta(seconds=2)).isoformat(), completed_at_utc=when.isoformat(),
                   start=dict(file=point["file"], position=point["position"], anchor=point["anchor"]),
                   end=dict(file=end["file"], position=end["position"], anchor=end), sources=[end])
        state = self.start(doc).data
        self.all_parts(state)
        self.assertEqual(self.finish(state).status_code, 200)
        self.documents[doc["incremental_id"]] = doc
        return HubBackup.objects.get(backup_uuid=state["backup_uuid"])

    def audit(self, registrar=False, now=None):
        return auditar_backups_hub(empresa_id=self.empresa.pk, loja_id=self.loja.pk, hub_id=self.hub.pk,
                                  registrar=registrar, agora=now or self.now)

    def indexed(self, report):
        return {row["backup_uuid"]: row for row in report["backups"]}

    def corrupt(self, backup):
        path = destination(backup)
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            value = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([value[0] ^ 1]))

    def test_dry_run_checks_full_and_incremental_without_writes_or_deletion(self):
        full = self.full()
        inc = self.incremental(full)
        files = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        before = list(HubBackup.objects.values())
        with patch.object(Path, "unlink", side_effect=AssertionError("no deletion")), patch.object(Path, "mkdir", side_effect=AssertionError("no directories")):
            report = self.audit()
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["restauracao_homologada"])
        self.assertTrue(all(row["integridade"] == "VALIDO" for row in report["backups"]))
        self.assertTrue(all(row["retencao"] == "MANTER" for row in report["backups"]))
        self.assertEqual(list(HubBackup.objects.values()), before)
        self.assertEqual(files, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_explicit_internal_registration_only_after_complete_verification(self):
        full = self.full()
        inc = self.incremental(full)
        self.audit(registrar=True)
        for backup in (full, inc):
            backup.refresh_from_db()
            self.assertEqual(backup.estado_validacao, "VALIDO")
            self.assertIsNotNone(backup.verificado_em)
        self.assertEqual(inc.situacao_cadeia(), "VALIDO")

    def test_corrupt_full_blocks_all_descendants_and_retention(self):
        full = self.full()
        inc = self.incremental(full)
        self.corrupt(full)
        report = self.indexed(self.audit(registrar=True))
        full.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual(full.estado_validacao, "INVALIDO")
        self.assertEqual(inc.estado_validacao, "CADEIA_INTERROMPIDA")
        for row in report.values():
            self.assertTrue(row["cadeia_bloqueada"])
            self.assertEqual(row["retencao"], "MANTER")
        self.assertTrue(destination(full).exists())

    def test_corrupt_incremental_preserves_valid_prefix_and_blocks_next(self):
        full = self.full()
        one = self.incremental(full, self.now-timedelta(days=3))
        two = self.incremental(one, self.now-timedelta(days=2))
        three = self.incremental(two)
        self.corrupt(two)
        data = self.indexed(self.audit(registrar=True))
        self.assertEqual(data[str(one.backup_uuid)]["integridade"], "VALIDO")
        self.assertEqual(data[str(two.backup_uuid)]["integridade"], "INVALIDO")
        self.assertEqual(data[str(three.backup_uuid)]["integridade"], "CADEIA_INTERROMPIDA")
        self.assertTrue(all(row["retencao"] == "MANTER" for row in data.values()))

    def test_missing_file_blocks_complete_chain_without_recreating_it(self):
        full = self.full()
        inc = self.incremental(full)
        # Move only a fictitious test file aside; the audit must not delete or recreate it.
        path = destination(full)
        preserved = path.with_name("fixture-preserved.bin")
        path.rename(preserved)
        data = self.indexed(self.audit())
        self.assertEqual(data[str(full.backup_uuid)]["codigo"], "FILE_OR_LOCK_MISSING")
        self.assertNotEqual(data[str(inc.backup_uuid)]["integridade"], "VALIDO")
        self.assertTrue(preserved.exists())
        self.assertFalse(path.exists())

    def test_sequence_gap_blocks_descendant(self):
        full = self.full()
        inc = self.incremental(full)
        # Out-of-band inconsistent metadata is simulated only in the isolated test DB.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE hub_hubbackup SET sequencia=2 WHERE id=%s", [inc.pk])
        data = self.indexed(self.audit())
        self.assertNotEqual(data[str(inc.backup_uuid)]["integridade"], "VALIDO")
        self.assertEqual(data[str(inc.backup_uuid)]["retencao"], "MANTER")

    def test_missing_key_is_inconclusive_and_does_not_expire_any_chain(self):
        full = self.full()
        self.incremental(full)
        with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=""):
            report = self.audit()
        self.assertTrue(all(row["integridade"] == "INCONCLUSIVO" for row in report["backups"]))
        self.assertTrue(all(row["retencao"] == "MANTER" for row in report["backups"]))
        full.refresh_from_db()
        self.assertEqual(full.estado_validacao, "PENDENTE")

    def test_unknown_historical_key_is_not_reported_as_corruption(self):
        self.full()
        import base64
        with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=json.dumps({"next": base64.b64encode(b"F"*32).decode()}), HUB_BACKUP_ACTIVE_KEY_ID="next"):
            report = self.audit()
        self.assertEqual(report["backups"][0]["integridade"], "INCONCLUSIVO")

    def test_pending_receipts_and_incomplete_uploads_are_always_kept(self):
        old = self.full(receive=False)
        new = self.full(self.now)
        report = self.audit(registrar=True)
        data = self.indexed(report)
        self.assertEqual(data[str(old.backup_uuid)]["retencao"], "MANTER")
        self.assertEqual(data[str(old.backup_uuid)]["integridade"], "PENDENTE")
        self.assertEqual(data[str(new.backup_uuid)]["integridade"], "VALIDO")

    def test_weekly_full_and_all_required_increments_are_preserved(self):
        full = self.full(self.now-timedelta(days=9))
        previous = self.incremental(full, self.now-timedelta(days=5))
        yesterday = self.incremental(previous, self.now-timedelta(days=1))
        today = self.incremental(yesterday, self.now)
        self.audit(registrar=True)
        report = self.audit()
        self.assertTrue(all(row["retencao"] == "MANTER" for row in report["backups"]))
        self.assertIn("DIA_ATUAL_ANTERIOR_OU_DEPENDENCIA", report["backups"][0]["motivos"])

    def test_only_obsolete_independent_chain_is_eligible_never_deleted(self):
        old = self.full(self.now-timedelta(days=10))
        old_inc = self.incremental(old, self.now-timedelta(days=8))
        new = self.full(self.now)
        self.audit(registrar=True)
        data = self.indexed(self.audit())
        self.assertEqual(data[str(old.backup_uuid)]["retencao"], "ELEGIVEL_REMOCAO")
        self.assertEqual(data[str(old_inc.backup_uuid)]["retencao"], "ELEGIVEL_REMOCAO")
        self.assertEqual(data[str(new.backup_uuid)]["retencao"], "MANTER")
        self.assertTrue(destination(old).exists())

    def test_last_old_intact_chain_is_protected_when_new_full_is_pending(self):
        old = self.full()
        self.audit(registrar=True)
        self.full(self.now, receive=False)
        data = self.indexed(self.audit())
        self.assertEqual(data[str(old.backup_uuid)]["retencao"], "MANTER")
        self.assertIn("ULTIMA_CADEIA_INTEGRA_DA_INSTALACAO_MYSQL", data[str(old.backup_uuid)]["motivos"])

    def test_midnight_uses_configured_timezone_not_utc_date(self):
        # 02:30 UTC is 23:30 on Oct 15 in Sao Paulo. Preserve Oct 14 local points.
        point = self.full(datetime(2026,10,14,12,tzinfo=dt_timezone.utc))
        self.full(datetime(2026,10,15,12,tzinfo=dt_timezone.utc))
        self.audit(registrar=True)
        before = self.indexed(self.audit(now=datetime(2026,10,16,2,30,tzinfo=dt_timezone.utc)))
        after = self.indexed(self.audit(now=datetime(2026,10,16,3,30,tzinfo=dt_timezone.utc)))
        self.assertEqual(before[str(point.backup_uuid)]["retencao"], "MANTER")
        self.assertEqual(after[str(point.backup_uuid)]["retencao"], "ELEGIVEL_REMOCAO")

    def test_previous_day_points_survive_new_weekly_full(self):
        prior = self.full(self.now-timedelta(days=7))
        point = self.incremental(prior, self.now-timedelta(days=1))
        self.full(self.now)
        self.audit(registrar=True)
        data = self.indexed(self.audit())
        self.assertEqual(data[str(prior.backup_uuid)]["retencao"], "MANTER")
        self.assertEqual(data[str(point.backup_uuid)]["retencao"], "MANTER")

    def test_each_mysql_identity_keeps_its_last_intact_chain(self):
        old = self.full(self.now-timedelta(days=10))
        self.mysql_uuid = str(uuid4())
        self.full(self.now)
        self.audit(registrar=True)
        self.assertEqual(self.indexed(self.audit())[str(old.backup_uuid)]["retencao"], "MANTER")

    def test_scope_rejects_other_store_before_storage_access(self):
        self.full()
        with patch("hub.backup_integrity._verify", side_effect=AssertionError("must not open files")):
            for loja_id, hub_id in ((self.loja_b.pk, self.hub_b.pk), (self.loja_a2.pk, self.hub.pk)):
                with self.assertRaises(ValidationError):
                    auditar_backups_hub(empresa_id=self.empresa.pk, loja_id=loja_id, hub_id=hub_id)

    def test_scope_never_loads_files_of_other_hubs(self):
        backup = self.full()
        from hub.backups import registrar_metadados_backup
        from hub import tests_backups
        foreign_data = tests_backups.HubBackupTests.metadados_full(self, self.hub_b)
        foreign, _ = registrar_metadados_backup(hub=self.hub_b, **foreign_data)
        report = self.audit()
        self.assertEqual([row["backup_uuid"] for row in report["backups"]], [str(backup.backup_uuid)])
        report = auditar_backups_hub(empresa_id=self.empresa_b.pk, loja_id=self.loja_b.pk, hub_id=self.hub_b.pk)
        self.assertEqual([row["backup_uuid"] for row in report["backups"]], [str(foreign.backup_uuid)])
        self.assertEqual(report["backups"][0]["integridade"], "PENDENTE")

    def test_busy_upload_lock_is_inconclusive_and_retryable(self):
        full = self.full()
        with backup_lock(full):
            report = self.audit()
        self.assertEqual(report["backups"][0]["integridade"], "INCONCLUSIVO")
        self.assertEqual(self.audit()["backups"][0]["integridade"], "VALIDO")

    def test_snapshot_change_rolls_back_all_validation_states(self):
        self.full()
        from hub import backup_integrity
        original = backup_integrity._file_identity
        calls = [0]
        def changed(path):
            calls[0] += 1
            fingerprint = original(path)
            return fingerprint if calls[0] < 3 else (*fingerprint[:-1], fingerprint[-1]+1)
        with patch("hub.backup_integrity._file_identity", side_effect=changed), self.assertRaises(IntegrityError):
            self.audit(registrar=True)
        self.assertEqual(HubBackup.objects.get().estado_validacao, "PENDENTE")

    def test_cli_is_always_dry_run_and_outputs_justifications(self):
        self.full()
        output = io.StringIO()
        call_command("auditar_backups_hub", empresa=self.empresa.pk, loja=self.loja.pk, hub=self.hub.pk, stdout=output)
        report = json.loads(output.getvalue())
        self.assertTrue(report["dry_run"])
        self.assertTrue(report["retencao_dry_run"])
        self.assertTrue(report["backups"][0]["motivos"])
        self.assertEqual(HubBackup.objects.get().estado_validacao, "PENDENTE")

    def test_audit_does_not_require_enabling_receiving(self):
        self.full()
        with override_settings(HUB_BACKUP_RECEBIMENTO_ATIVO=False):
            self.assertEqual(self.audit()["backups"][0]["integridade"], "VALIDO")

    def test_streaming_verifier_is_reused_once_per_file(self):
        self.full()
        from hub import backup_integrity
        with patch("hub.backup_integrity._verify_file", wraps=backup_integrity._verify_file) as verify:
            self.audit()
        self.assertEqual(verify.call_count, 1)


    def test_changed_file_during_dry_run_keeps_everything(self):
        self.full()
        from hub import backup_integrity
        original = backup_integrity._file_identity
        count = [0]
        def changed(path):
            count[0] += 1
            identity = original(path)
            return identity if count[0] < 3 else (*identity[:-1], identity[-1]+1)
        with patch("hub.backup_integrity._file_identity", side_effect=changed):
            report = self.audit()
        self.assertEqual(report["backups"][0]["codigo"], "SNAPSHOT_CHANGED_RETRY")
        self.assertEqual(report["backups"][0]["retencao"], "MANTER")

    def test_previous_interruption_requires_fresh_complete_proof(self):
        full = self.full()
        inc = self.incremental(full)
        self.corrupt(full)
        self.audit(registrar=True)
        self.corrupt(full)  # Undo the fictitious bit flip; no real restore is performed.
        self.audit(registrar=True)
        full.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual((full.estado_validacao, inc.estado_validacao), ("VALIDO", "VALIDO"))

    def test_future_dates_cannot_make_files_eligible(self):
        future = self.full(self.now + timedelta(days=3))
        self.full(self.now + timedelta(days=4))
        self.audit(registrar=True)
        self.assertEqual(self.indexed(self.audit())[str(future.backup_uuid)]["retencao"], "MANTER")

    def test_pending_validation_kept_even_if_dry_run_proves_integrity(self):
        old = self.full()
        self.full(self.now)
        report = self.indexed(self.audit())
        self.assertEqual(report[str(old.backup_uuid)]["integridade"], "VALIDO")
        self.assertEqual(report[str(old.backup_uuid)]["estado_validacao"], "PENDENTE")
        self.assertEqual(report[str(old.backup_uuid)]["retencao"], "MANTER")

    def test_missing_lock_never_creates_a_new_one_in_dry_run(self):
        full = self.full()
        final = destination(full)
        lock = final.with_name(".backup-"+full.backup_uuid.hex+".lock")
        preserved = lock.with_suffix(".fixture")
        lock.rename(preserved)
        self.assertEqual(self.audit()["backups"][0]["integridade"], "CADEIA_INTERROMPIDA")
        self.assertFalse(lock.exists())
        self.assertTrue(preserved.exists())


    def test_historical_installation_remains_verifiable_after_repairing(self):
        from hub.models import SysvarHub
        old = self.full()
        inc = self.incremental(old)
        SysvarHub.objects.filter(pk=self.hub.pk).update(hub_uuid=uuid4())
        data = self.indexed(self.audit(registrar=True))
        self.assertEqual(data[str(old.backup_uuid)]["integridade"], "VALIDO")
        self.assertEqual(data[str(inc.backup_uuid)]["integridade"], "VALIDO")
        self.assertTrue(all(row["retencao"] == "MANTER" for row in data.values()))


    def test_failed_registration_rolls_back_previous_state_changes(self):
        full = self.full()
        inc = self.incremental(full)
        original = HubBackup.save
        def fail_second(instance, *args, **kwargs):
            if instance.pk == inc.pk:
                raise OSError("fictitious registration failure")
            return original(instance, *args, **kwargs)
        with patch.object(HubBackup, "save", fail_second), self.assertRaises(OSError):
            self.audit(registrar=True)
        full.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual((full.estado_validacao, inc.estado_validacao), ("PENDENTE", "PENDENTE"))


    def test_clock_reversal_cannot_expire_suffix_of_protected_chain(self):
        old = self.full(self.now-timedelta(days=7))
        yesterday = self.incremental(old, self.now-timedelta(days=1))
        suffix = self.incremental(yesterday, self.now-timedelta(days=3))
        self.full(self.now)
        self.audit(registrar=True)
        row = self.indexed(self.audit())[str(suffix.backup_uuid)]
        self.assertEqual(row["retencao"], "MANTER")
        self.assertIn("CADEIA_CONTEM_PONTO_PROTEGIDO", row["motivos"])


class BackupTimezoneTests(SimpleTestCase):
    @override_settings(HUB_BACKUP_RETENTION_TIME_ZONE="Invalid/Timezone")
    def test_invalid_timezone_fails_closed(self):
        from hub.backup_integrity import retention_timezone
        from hub.checks import check_hub_backup_retention_timezone
        with self.assertRaises(ImproperlyConfigured):
            retention_timezone()
        self.assertEqual(check_hub_backup_retention_timezone(None)[0].id, "hub.E003")

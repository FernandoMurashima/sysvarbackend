import base64
import gzip
import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
from datetime import timedelta

from django.db import OperationalError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Empresa, Loja
from hub.backup_crypto import MAGIC, cipher_upload, conferir_header, header_upload, ler_registro
from hub.backup_files import backup_lock, destination, open_private, proteger_novo, sem_links, stage_path
from hub import backup_uploads
from hub.models import HubBackup, HubBackupParte, HubBackupUpload, SysvarHub


class SecureAPIClient(APIClient):
    def generic(self, method, path, data="", content_type="application/octet-stream", secure=True, **extra):
        return super().generic(method, path, data, content_type, secure=secure, **extra)


class BackupRecebimentoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nome="Backup API A", documento="11222333000181")
        cls.empresa_b = Empresa.objects.create(nome="Backup API B", documento="21222333000181")
        cls.loja = Loja.objects.create(empresa=cls.empresa, nome_loja="Loja A", apelido_loja="A", cnpj="11222333000181", estado="SP")
        cls.loja_a2 = Loja.objects.create(empresa=cls.empresa, nome_loja="Loja A2", apelido_loja="A2", cnpj="31222333000181", estado="SP")
        cls.loja_b = Loja.objects.create(empresa=cls.empresa_b, nome_loja="Loja B", apelido_loja="B", cnpj="21222333000181", estado="SP")
        cls.hub = SysvarHub.objects.create(loja=cls.loja)
        cls.hub_a2 = SysvarHub.objects.create(loja=cls.loja_a2)
        cls.hub_b = SysvarHub.objects.create(loja=cls.loja_b)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="sysvar-upload-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.cleanup_files)
        proteger_novo(self.root, directory=True)
        self.key = os.urandom(32)
        self.keys_json = json.dumps({"test-key": base64.b64encode(self.key).decode("ascii")})
        self.override = override_settings(HUB_BACKUP_RECEBIMENTO_ATIVO=True, HUB_BACKUP_ROOT=self.root,
            HUB_BACKUP_ENCRYPTION_KEYS=self.keys_json, HUB_BACKUP_ACTIVE_KEY_ID="test-key",
            HUB_BACKUP_PART_BYTES=16, HUB_BACKUP_MAX_FILE_BYTES=1024**2, HUB_BACKUP_MIN_FREE_BYTES=0,
            HUB_BACKUP_PUBLIC_ROOTS=[], HUB_BACKUP_IDLE_SECONDS=3600, HUB_BACKUP_MAX_MANIFEST_BYTES=1024**2)
        self.override.enable(); self.addCleanup(self.override.disable)
        self.client = self.client_for(self.hub)
        self.payload = gzip.compress(b"dados e operacoes exclusivamente ficticios para teste " * 5, mtime=0)
        self.manifest = self.full_manifest(self.payload)

    def cleanup_files(self):
        target = Path(self.temp.name).resolve()
        if target.parent != Path(gettempdir()).resolve() or not target.name.startswith("sysvar-upload-test-"):
            raise AssertionError("Alvo temporario inesperado")
        self.temp.cleanup()

    def client_for(self, hub):
        client = SecureAPIClient()
        client.credentials(HTTP_AUTHORIZATION="Hub " + hub.gerar_token())
        return client

    def full_manifest(self, payload):
        anchor = dict(algorithm="MYSQL8_CRC32_PREFIX_V1", file="sysvarhub-bin.000001", position=100, sha256="a" * 64)
        now = timezone.now().isoformat()
        return dict(manifest_version=1, backup_id=uuid4().hex, type="FULL", status="VALIDO", started_at_utc=now,
            completed_at_utc=now, installation=dict(hub_uuid=str(self.hub.hub_uuid), empresa_id=self.empresa.pk,
            loja_id=self.loja.pk, retaguarda_hub_id=self.hub.pk), mysql=dict(version="8.0.46", database="sysvarhub_db",
            server_uuid=str(uuid4()), server_id=1, binlog_format="ROW"), binlog=dict(file=anchor["file"], position=100,
            reference="SNAPSHOT_START", anchor=anchor), archive=dict(name="sysvarhub_db.sql.gz", size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(), compression="gzip", sql_size_bytes=260))

    def raw(self, doc):
        return json.dumps(doc, ensure_ascii=False, indent=2)

    def start(self, doc=None, client=None):
        return (client or self.client).post(reverse("hub-backup-iniciar"), {"manifesto": self.raw(doc or self.manifest)}, format="json")

    def part(self, state, index, payload=None, **headers):
        data = self.payload if payload is None else payload
        chunk = data[index * state["tamanho_parte"]:(index + 1) * state["tamanho_parte"]]
        args = dict(HTTP_X_UPLOAD_ID=state["upload_uuid"], HTTP_X_UPLOAD_OFFSET=str(index * state["tamanho_parte"]),
                    HTTP_X_CHUNK_SHA256=hashlib.sha256(chunk).hexdigest())
        args.update(headers)
        return self.client.generic("PUT", reverse("hub-backup-parte", args=[state["backup_uuid"], index]), chunk,
                                   content_type="application/octet-stream", **args)

    def all_parts(self, state, payload=None):
        payload = self.payload if payload is None else payload
        for index in range((len(payload) + state["tamanho_parte"] - 1) // state["tamanho_parte"]):
            response = self.part(state, index, payload)
            self.assertEqual(response.status_code, 200, response.data)

    def finish(self, state):
        return self.client.post(reverse("hub-backup-finalizar", args=[state["backup_uuid"]]), {"upload_uuid": state["upload_uuid"]}, format="json")

    def full_received(self):
        response = self.start(); self.assertEqual(response.status_code, 200, response.data)
        state = response.data; self.all_parts(state)
        response = self.finish(state); self.assertEqual(response.status_code, 200, response.data)
        return state

    def test_autenticacao_invalida_ausente_revogada(self):
        for token in (None, "Hub invalido"):
            client = APIClient()
            if token:
                client.credentials(HTTP_AUTHORIZATION=token)
            self.assertEqual(client.post(reverse("hub-backup-iniciar"), {"manifesto": self.raw(self.manifest)}, format="json").status_code, 401)
        self.hub.gerar_token()
        self.assertEqual(self.start().status_code, 401)
        self.assertEqual(HubBackup.objects.count(), 0)

    def test_https_obrigatorio_mesmo_com_token_valido(self):
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION="Hub " + self.hub.gerar_token())
        body = {"manifesto": self.raw(self.manifest)}
        self.assertEqual(client.post(reverse("hub-backup-iniciar"), body, format="json").status_code, 403)
        self.assertEqual(HubBackup.objects.count(), 0)
        self.assertEqual(client.post(reverse("hub-backup-iniciar"), body, format="json", secure=True).status_code, 200)

    def test_recebimento_desativado_por_padrao_e_sem_criptografia_falha_fechado(self):
        with override_settings(HUB_BACKUP_RECEBIMENTO_ATIVO=False):
            self.assertEqual(self.start().status_code, 503)
        with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=""):
            result = self.start(); self.assertEqual(result.status_code, 503)
            self.assertNotIn(self.key.hex(), str(result.data))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(HubBackup.objects.count(), 0)

    def test_full_em_partes_criptografado_e_recebido_nao_valido(self):
        state = self.full_received()
        backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"])
        upload = backup.uploads.first()
        self.assertEqual((backup.estado_recebimento, backup.estado_validacao, upload.estado), ("RECEBIDO", "PENDENTE", "RECEBIDO"))
        self.assertIsNone(backup.verificado_em)
        final = destination(backup)
        ciphertext = final.read_bytes()
        self.assertTrue(ciphertext.startswith(MAGIC))
        self.assertNotIn(self.payload, ciphertext)
        self.assertNotIn(self.raw(self.manifest).encode(), ciphertext)
        self.assertFalse(stage_path(upload).exists())
        header = header_upload(upload); cipher = cipher_upload(upload, {"test-key": self.key})
        with open_private(final) as stream:
            conferir_header(stream, header)
            self.assertEqual(ler_registro(stream, cipher, header, -1, upload.manifesto_tamanho), self.raw(self.manifest).encode())
            restored = b"".join(ler_registro(stream, cipher, header, part.indice, part.tamanho) for part in upload.partes.all())
        self.assertEqual(restored, self.payload)

    def test_isolamento_por_empresa_e_loja_em_todas_as_rotas(self):
        state = self.start().data
        for hub in (self.hub_a2, self.hub_b):
            client = self.client_for(hub)
            for method, url, body in (
                ("get", reverse("hub-backup-progresso", args=[state["backup_uuid"]]), None),
                ("post", reverse("hub-backup-finalizar", args=[state["backup_uuid"]]), {"upload_uuid": state["upload_uuid"]}),
            ):
                response = getattr(client, method)(url, body, format="json")
                self.assertEqual(response.status_code, 404)
            response = client.generic("PUT", reverse("hub-backup-parte", args=[state["backup_uuid"], 0]), self.payload[:16],
                content_type="application/octet-stream", HTTP_X_UPLOAD_ID=state["upload_uuid"], HTTP_X_UPLOAD_OFFSET="0",
                HTTP_X_CHUNK_SHA256=hashlib.sha256(self.payload[:16]).hexdigest())
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.start(client=client).status_code, 400)
        self.assertEqual(HubBackup.objects.count(), 1)

    def test_ids_caminhos_e_instalacao_livres_rejeitados(self):
        for mutate in (lambda d: d["installation"].update(empresa_id=self.empresa_b.pk),
                       lambda d: d["installation"].update(loja_id=self.loja_a2.pk),
                       lambda d: d["installation"].update(hub_uuid=str(uuid4())),
                       lambda d: d["archive"].update(name="../../publico.sql.gz")):
            doc = deepcopy(self.manifest); mutate(doc)
            self.assertEqual(self.start(doc).status_code, 400)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_interrupcao_retomada_e_reenvio_idempotente(self):
        state = self.start().data
        self.assertEqual(self.part(state, 0).status_code, 200)
        count = HubBackupParte.objects.count()
        self.assertEqual(self.part(state, 0).status_code, 200)
        self.assertEqual(HubBackupParte.objects.count(), count)
        resumed = self.start().data
        self.assertEqual(resumed["upload_uuid"], state["upload_uuid"])
        self.assertEqual(resumed["offset"], 16)
        status = self.client.get(reverse("hub-backup-progresso", args=[state["backup_uuid"]]))
        self.assertEqual(status.data["offset"], 16)
        self.all_parts(state)
        self.assertEqual(self.finish(state).status_code, 200)
        backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"]); before = destination(backup).read_bytes()
        self.assertEqual(self.finish(state).status_code, 200)
        self.assertEqual(self.start().data["upload_uuid"], state["upload_uuid"])
        self.assertEqual(destination(backup).read_bytes(), before)

    def test_reenvio_de_parte_confirmada_apos_finalizacao_tambem_e_idempotente(self):
        state = self.full_received(); backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"])
        saved = destination(backup).read_bytes(); count = HubBackupParte.objects.count()
        self.assertEqual(self.part(state, 0).status_code, 200)
        self.assertEqual(HubBackupParte.objects.count(), count)
        self.assertEqual(destination(backup).read_bytes(), saved)

    def test_configuracao_de_chave_invalida_nao_cria_arquivos(self):
        for ring in ('{"test-key":"invalido"}', '{"test-key":"AA=="}', '{"test-key":"AA==","test-key":"AA=="}'):
            with self.subTest(ring=ring), override_settings(HUB_BACKUP_ENCRYPTION_KEYS=ring):
                self.assertEqual(self.start().status_code, 503)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(HubBackup.objects.count(), 0)

    def test_manifesto_divergente_e_json_duplicado_rejeitados(self):
        self.assertEqual(self.start().status_code, 200)
        changed = deepcopy(self.manifest); changed["archive"]["sha256"] = "e" * 64
        self.assertEqual(self.start(changed).status_code, 400)
        response = self.client.post(reverse("hub-backup-iniciar"), b'{"manifesto":"a","manifesto":"b"}', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(HubBackupUpload.objects.count(), 1)

    def test_parte_conflitante_fora_de_ordem_hash_errado_e_upload_uuid_ausente(self):
        state = self.start().data
        self.assertEqual(self.part(state, 1).status_code, 409)
        self.assertEqual(self.part(state, 0, HTTP_X_CHUNK_SHA256="f" * 64).status_code, 422)
        self.assertEqual(self.part(state, 0, HTTP_X_UPLOAD_ID="").status_code, 400)
        self.assertEqual(self.part(state, 0).status_code, 200)
        bad = b"x" * len(self.payload)
        self.assertEqual(self.part(state, 0, bad).status_code, 409)
        self.assertEqual(HubBackupParte.objects.count(), 1)

    def test_upload_incompleto_e_timeout_retoma_mesma_tentativa(self):
        state = self.start().data; self.part(state, 0)
        self.assertEqual(self.finish(state).status_code, 409)
        HubBackupUpload.objects.filter(upload_uuid=state["upload_uuid"]).update(expira_em=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.part(state, 1).status_code, 408)
        resumed = self.start().data
        self.assertEqual(resumed["upload_uuid"], state["upload_uuid"])
        self.assertEqual(resumed["offset"], 16)
        self.assertEqual(self.part(state, 1).status_code, 200)

    def test_limites_parte_total_manifesto_e_midia(self):
        with override_settings(HUB_BACKUP_MAX_FILE_BYTES=1):
            self.assertEqual(self.start().status_code, 413)
        with override_settings(HUB_BACKUP_MAX_MANIFEST_BYTES=10):
            self.assertEqual(self.start().status_code, 413)
        state = self.start().data
        self.assertEqual(self.part(state, 0, CONTENT_LENGTH="9999").status_code, 413)
        response = self.client.put(reverse("hub-backup-parte", args=[state["backup_uuid"], 0]), {}, format="json")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(HubBackupParte.objects.count(), 0)

    def test_checksum_total_incorreto_nao_publica_e_nova_tentativa_preserva_anterior(self):
        doc = deepcopy(self.manifest); doc["archive"]["sha256"] = "f" * 64
        state = self.start(doc).data; self.all_parts(state)
        self.assertEqual(self.finish(state).status_code, 422)
        backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"])
        self.assertFalse(destination(backup).exists())
        previous = backup.uploads.first(); partial = stage_path(previous); saved = partial.read_bytes()
        retry = self.start(doc)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertNotEqual(retry.data["upload_uuid"], state["upload_uuid"])
        self.assertEqual(retry.data["offset"], 0)
        self.assertEqual(partial.read_bytes(), saved)
        self.assertEqual(backup.uploads.count(), 2)

    def test_disputa_de_upload_bloqueada_sem_avanco(self):
        state = self.start().data; backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"])
        with backup_lock(backup):
            self.assertEqual(self.part(state, 0).status_code, 409)
        self.assertEqual(HubBackupParte.objects.count(), 0)

    def test_falta_espaco_e_falha_gravacao_nao_avancam_checkpoint(self):
        state = self.start().data
        with patch("hub.backup_uploads.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            self.assertEqual(self.part(state, 0).status_code, 507)
        with patch("hub.backup_uploads.os.fsync", side_effect=OSError(28, "DISCO FICTICIO")):
            self.assertEqual(self.part(state, 0).status_code, 507)
        upload = HubBackupUpload.objects.get(upload_uuid=state["upload_uuid"])
        self.assertEqual(upload.bytes_recebidos, 0)
        self.assertEqual(HubBackupParte.objects.count(), 0)
        self.assertEqual(self.part(state, 0).status_code, 200)

    def test_crash_apos_write_antes_checkpoint_descarta_apenas_cauda_temporaria(self):
        state = self.start().data
        with patch("hub.backup_uploads.HubBackupParte.objects.create", side_effect=OperationalError("falha ficticia")):
            self.assertEqual(self.part(state, 0).status_code, 503)
        self.assertEqual(HubBackupParte.objects.count(), 0)
        self.all_parts(state)
        self.assertEqual(self.finish(state).status_code, 200)

    def test_publicacao_interrompida_antes_commit_e_reconciliada_sem_sobrescrever(self):
        state = self.start().data; self.all_parts(state)
        original = backup_uploads.publish
        def interrupted(upload):
            original(upload)
            raise OSError("interrupcao ficticia depois da publicacao")
        with patch("hub.backup_uploads.publish", side_effect=interrupted):
            self.assertEqual(self.finish(state).status_code, 503)
        backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"]); path = destination(backup); content = path.read_bytes()
        self.assertEqual(self.finish(state).status_code, 200)
        self.assertEqual(path.read_bytes(), content)

    def test_arquivo_publicado_divergente_nunca_e_sobrescrito(self):
        state = self.start().data; self.all_parts(state)
        backup = HubBackup.objects.get(backup_uuid=state["backup_uuid"]); final = destination(backup)
        final.write_bytes(b"arquivo integro anterior ficticio que nao pertence a esta tentativa")
        saved = final.read_bytes()
        self.assertEqual(self.finish(state).status_code, 409)
        self.assertEqual(final.read_bytes(), saved)

    def test_tag_criptografica_corrompida_rejeitada_sem_publicacao(self):
        state = self.start().data; self.all_parts(state)
        upload = HubBackupUpload.objects.get(upload_uuid=state["upload_uuid"]); path = stage_path(upload)
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
        self.assertEqual(self.finish(state).status_code, 409)
        self.assertFalse(destination(upload.backup).exists())

    def test_rotacao_chave_preserva_chave_historica_e_sem_fallback(self):
        state = self.start().data
        ring = json.loads(self.keys_json); ring["next-key"] = base64.b64encode(os.urandom(32)).decode()
        with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=json.dumps(ring), HUB_BACKUP_ACTIVE_KEY_ID="next-key"):
            self.all_parts(state)
            with override_settings(HUB_BACKUP_ENCRYPTION_KEYS=json.dumps({"next-key": ring["next-key"]})):
                self.assertEqual(self.finish(state).status_code, 503)
            self.assertEqual(self.finish(state).status_code, 200)

    def test_corrupcao_detectada_de_arquivo_recebido_sinaliza_cadeia_sem_apagar(self):
        state = self.full_received(); upload = HubBackupUpload.objects.first(); backup = upload.backup
        backup.estado_validacao = "VALIDO"; backup.verificado_em = timezone.now(); backup.save()
        path = destination(backup); damaged = bytearray(path.read_bytes()); damaged[-1] ^= 1; path.write_bytes(damaged)
        self.assertEqual(self.finish(state).status_code, 409)
        backup.refresh_from_db()
        self.assertEqual((backup.estado_recebimento, backup.estado_validacao), ("RECEBIDO", "CADEIA_INTERROMPIDA"))
        self.assertEqual(path.read_bytes(), bytes(damaged))

    def test_progresso_nao_pode_ser_cacheado(self):
        state = self.start().data
        result = self.client.get(reverse("hub-backup-progresso", args=[state["backup_uuid"]]))
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(result["Content-Type"], "application/json")

    def incremental(self, full_state, payload):
        doc = deepcopy(self.manifest)
        doc.pop("backup_id"); point = doc.pop("binlog")
        end_anchor = dict(algorithm="MYSQL8_CRC32_PREFIX_V1", file=point["file"], position=200, sha256="b" * 64)
        doc.update(type="INCREMENTAL", incremental_id=uuid4().hex, sequence=1,
            full_backup_id=full_state["backup_uuid"], full_manifest_sha256=hashlib.sha256(self.raw(self.manifest).encode()).hexdigest(),
            previous_id=full_state["backup_uuid"], previous_manifest_sha256=hashlib.sha256(self.raw(self.manifest).encode()).hexdigest(),
            capture_format="MYSQLBINLOG_SQL_BOUNDED_V1", coordinate_semantics="START_INCLUSIVE_END_EXCLUSIVE",
            start={"file": point["file"], "position": 100, "anchor": point["anchor"]},
            end={"file": point["file"], "position": 200, "anchor": end_anchor}, sources=[end_anchor])
        doc["mysql"].pop("server_id"); doc["mysql"].pop("binlog_format")
        doc["archive"] = dict(name="binlog-segment.sql.gz", size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), compression="gzip")
        return doc

    def test_incremental_vinculado_ao_full_recebido(self):
        full = self.full_received(); payload = gzip.compress(b"alteracoes ficticias", mtime=0)
        doc = self.incremental(full, payload); response = self.start(doc)
        self.assertEqual(response.status_code, 200, response.data)
        state = response.data; self.all_parts(state, payload)
        self.assertEqual(self.finish(state).status_code, 200)
        inc = HubBackup.objects.get(backup_uuid=state["backup_uuid"])
        self.assertEqual(str(inc.full_referencia.backup_uuid), full["backup_uuid"])
        self.assertEqual(inc.estado_validacao, "PENDENTE")

    def test_incremental_sequencia_hash_ancora_ou_servidor_invalidos(self):
        full = self.full_received()
        for modify in (lambda d: d.update(sequence=2), lambda d: d.update(previous_manifest_sha256="e" * 64),
                       lambda d: d["start"]["anchor"].update(sha256="e" * 64),
                       lambda d: d["mysql"].update(server_uuid=str(uuid4()))):
            doc = self.incremental(full, self.payload); modify(doc)
            self.assertIn(self.start(doc).status_code, (400, 409))
        self.assertEqual(HubBackup.objects.count(), 1)

    def test_storage_publico_symlink_e_reparse_bloqueados(self):
        with override_settings(HUB_BACKUP_ROOT=Path(__file__).parent):
            self.assertEqual(self.start().status_code, 503)
        original = Path.lstat
        def reparse(path):
            value = original(path)
            if path == self.root:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with patch.object(Path, "lstat", reparse):
            self.assertEqual(self.start().status_code, 503)
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)):
            with self.assertRaises(backup_uploads.BackupIOError):
                sem_links(self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stream_limitado_a_uma_parte_sem_leitura_do_backup_inteiro(self):
        from hub import backup_crypto
        state = self.start().data; self.all_parts(state)
        original = backup_crypto.ler_exato
        reads = []
        def bounded(stream, length):
            reads.append(length)
            self.assertLessEqual(length, max(len(header_upload(HubBackupUpload.objects.first())), len(self.raw(self.manifest).encode())) + 28)
            return original(stream, length)
        with patch("hub.backup_crypto.ler_exato", side_effect=bounded):
            self.assertEqual(self.finish(state).status_code, 200)
        self.assertGreater(len(reads), HubBackupParte.objects.count())

    def test_interrupcao_de_conexao_nao_avanca_offset(self):
        state = self.start().data
        fake = SimpleNamespace(read=lambda n: (_ for _ in ()).throw(TimeoutError()))
        with self.assertRaises(backup_uploads.RecebimentoErro) as caught:
            backup_uploads.enviar_parte(self.hub, state["backup_uuid"], state["upload_uuid"], 0, 0,
                hashlib.sha256(self.payload[:16]).hexdigest(), fake, 16)
        self.assertEqual(caught.exception.status, 408)
        self.assertEqual(HubBackupParte.objects.count(), 0)
        self.assertEqual(self.part(state, 0).status_code, 200)

    def test_timeout_de_verificacao_nao_publica_e_permite_retry(self):
        state = self.start().data; self.all_parts(state)
        with patch("hub.backup_uploads.time.monotonic", side_effect=[0, 10000]):
            self.assertEqual(self.finish(state).status_code, 408)
        upload = HubBackupUpload.objects.first()
        self.assertEqual(upload.estado, "ABERTO")
        self.assertFalse(destination(upload.backup).exists())
        self.assertEqual(self.finish(state).status_code, 200)

    def test_erro_sem_conteudo_credenciais_ou_chave_nos_logs(self):
        state = self.start().data
        with self.assertLogs("hub.backup_uploads", level="WARNING") as logs:
            with patch("hub.backup_uploads.os.fsync", side_effect=OSError(28, "SEGREDO-FICTICIO-NAO-REGISTRAR")):
                result = self.part(state, 0)
        self.assertEqual(result.status_code, 507)
        captured = " ".join(logs.output) + str(result.data)
        self.assertNotIn("SEGREDO-FICTICIO-NAO-REGISTRAR", captured)
        self.assertNotIn(base64.b64encode(self.key).decode(), captured)
        self.assertNotIn(self.payload.hex(), captured)

    def test_arquivo_recebido_e_preservado_se_verificacao_posterior_falhar(self):
        state = self.full_received()
        upload = HubBackupUpload.objects.first(); backup = upload.backup; path = destination(backup); original = path.read_bytes()
        with patch("hub.backup_uploads._verify_file", side_effect=OSError(28, "falha ficticia")):
            self.assertEqual(self.finish(state).status_code, 507)
        backup.refresh_from_db()
        self.assertEqual(backup.estado_recebimento, "RECEBIDO")
        self.assertEqual(path.read_bytes(), original)

    def test_reinstalacao_nao_reutiliza_upload_da_instalacao_anterior(self):
        state = self.start().data
        self.hub.hub_uuid = uuid4(); self.hub.save(update_fields=["hub_uuid"])
        self.assertEqual(self.client.get(reverse("hub-backup-progresso", args=[state["backup_uuid"]])).status_code, 404)
        self.assertEqual(self.part(state, 0).status_code, 404)



class BackupChaveLimiteTests(SimpleTestCase):
    def test_chave_maxima_249_caracteres_e_unique_255(self):
        maximum, uuid = 2**63 - 1, UUID("f" * 32)
        backup = HubBackup(empresa_id=maximum, loja_id=maximum, hub_id=maximum, tipo="INCREMENTAL",
                           instalacao_uuid=uuid, servidor_mysql_uuid=uuid, backup_uuid=uuid, full_referencia_id=1)
        with patch("hub.models.HubBackup.objects.filter") as query:
            query.return_value.first.return_value = SimpleNamespace(backup_uuid=uuid)
            self.assertEqual(len(backup.gerar_chave_armazenamento()), 249)
        field = HubBackup._meta.get_field("chave_armazenamento")
        self.assertEqual(field.max_length, 255)
        self.assertTrue(field.unique)

    def test_verificador_mysql_nao_emite_w003_para_chave(self):
        from django.db.backends.mysql.validation import DatabaseValidation
        validation = DatabaseValidation(SimpleNamespace(display_name="MySQL", _limited_data_types=set()))
        field = HubBackup._meta.get_field("chave_armazenamento")
        self.assertNotIn("mysql.W003", [issue.id for issue in validation.check_field_type(field, "varchar(255)")])

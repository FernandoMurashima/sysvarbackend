"""Recebimento resumivel. Arquivos recebidos aguardam validacao independente."""
import base64
import contextlib
import hashlib
import hmac
import logging
import os
import re
import shutil
import time
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, transaction
from django.utils import timezone

from .backup_crypto import (cipher_upload, conferir_header, configuracao_criptografia, escrever_header,
                            escrever_registro, header_upload, ler_registro)
from .backup_files import (BackupIOError, backup_lock, destination, open_private, publish, stage_path, storage_root)
from .backup_manifest import json_sem_duplicatas, ler_manifesto
from .backups import registrar_metadados_backup
from .models import HubBackup, HubBackupParte, HubBackupUpload, SysvarHub

logger = logging.getLogger(__name__)


class RecebimentoErro(BackupIOError):
    def __init__(self, code, status=409, terminal=False):
        super().__init__(code, status)
        self.terminal = terminal


def configuracao_recebimento():
    if not settings.HUB_BACKUP_RECEBIMENTO_ATIVO:
        raise RecebimentoErro("RECEIVING_DISABLED", 503)
    values = (
        (settings.HUB_BACKUP_PART_BYTES, 1, 16 * 1024**2),
        (settings.HUB_BACKUP_MAX_FILE_BYTES, 1, 1024**4),
        (settings.HUB_BACKUP_MAX_MANIFEST_BYTES, 1, 1024**2),
        (settings.HUB_BACKUP_MIN_FREE_BYTES, 0, 1024**4),
        (settings.HUB_BACKUP_IDLE_SECONDS, 1, 30 * 86400),
        (settings.HUB_BACKUP_FINALIZE_SECONDS, 1, 86400),
    )
    if any(type(value) is not int or not low <= value <= high for value, low, high in values):
        raise ImproperlyConfigured("Limites de recebimento invalidos.")
    keys = configuracao_criptografia()
    storage_root()
    return keys


def _hub(hub, lock=False):
    query = SysvarHub.objects.select_related("loja")
    if lock:
        query = query.select_for_update()
    current = query.filter(pk=hub.pk, token_hash=hub.token_hash, ativo=True).first()
    if not current:
        raise RecebimentoErro("HUB_CREDENTIAL_INVALID", 401)
    return current


def _backup(hub, backup_uuid):
    current = _hub(hub)
    backup = HubBackup.objects.do_hub(current).filter(backup_uuid=backup_uuid, instalacao_uuid=current.hub_uuid).first()
    if not backup:
        raise RecebimentoErro("BACKUP_NOT_FOUND", 404)
    return backup


def _latest(backup, upload_uuid=None):
    upload = backup.uploads.select_related("backup").first()
    try:
        matches = upload is not None and (upload_uuid is None or UUID(str(upload_uuid)) == upload.upload_uuid)
    except (ValueError, TypeError, AttributeError):
        matches = False
    if not matches:
        raise RecebimentoErro("UPLOAD_CONFLICT", 409)
    return upload


def _progress(upload):
    backup = upload.backup
    return dict(backup_uuid=str(backup.backup_uuid), upload_uuid=str(upload.upload_uuid), tentativa=upload.tentativa,
                offset=upload.bytes_recebidos, proxima_parte=upload.partes_recebidas, tamanho_parte=upload.tamanho_parte,
                tamanho_total=backup.tamanho_bytes, estado=upload.estado, codigo_erro=upload.codigo_erro,
                estado_recebimento=backup.estado_recebimento, estado_validacao=backup.estado_validacao,
                expira_em=upload.expira_em.isoformat())


def _space(required):
    if shutil.disk_usage(storage_root()).free < required + settings.HUB_BACKUP_MIN_FREE_BYTES:
        raise RecebimentoErro("INSUFFICIENT_SPACE", 507)


def _read_manifest(upload, keys, path):
    header = header_upload(upload)
    with open_private(path) as stream:
        conferir_header(stream, header)
        raw = ler_registro(stream, cipher_upload(upload, keys), header, -1, upload.manifesto_tamanho)
        if hashlib.sha256(raw).hexdigest() != upload.backup.manifesto_sha256:
            raise RecebimentoErro("MANIFEST_CONFLICT", 409, True)
        return raw, stream.tell()


def _check_predecessor(doc, metadata, keys):
    if doc["type"] != "INCREMENTAL":
        return
    previous = HubBackup.objects.get(pk=metadata["predecessor_id"])
    if HubBackup.objects.filter(full_referencia_id=metadata["full_referencia_id"], sequencia__lte=previous.sequencia,
                                estado_validacao__in=["INVALIDO", "CADEIA_INTERROMPIDA"]).exists():
        raise RecebimentoErro("CHAIN_INTERRUPTED", 409)
    upload = previous.uploads.filter(estado="RECEBIDO").select_related("backup").first()
    if not upload:
        raise RecebimentoErro("PREDECESSOR_NOT_RECEIVED", 409)
    raw, _ = _read_manifest(upload, keys, destination(previous))
    previous_doc = json_sem_duplicatas(raw.decode("utf-8-sig"))
    point = previous_doc["binlog"] if previous.tipo == "FULL" else previous_doc["end"]
    if doc["start"]["anchor"] != point["anchor"]:
        raise RecebimentoErro("PREDECESSOR_ANCHOR_CONFLICT", 409)


def _failure(upload, error):
    # Recebimento preservado; corrupcao comprovada interrompe a cadeia. Nenhum arquivo e removido.
    try:
        with transaction.atomic():
            SysvarHub.objects.select_for_update().get(pk=upload.backup.hub_id)
            current = HubBackupUpload.objects.select_for_update().get(pk=upload.pk)
            current.codigo_erro = error.code
            if getattr(error, "terminal", False) and current.estado != "RECEBIDO":
                current.estado = "ERRO"
            current.save(update_fields=["codigo_erro", "estado", "atualizado_em"])
            backup = HubBackup.objects.get(pk=upload.backup_id)
            if backup.estado_recebimento == "RECEBIDO" and getattr(error, "terminal", False):
                backup.estado_validacao = HubBackup.CADEIA_INTERROMPIDA
                backup.save(update_fields=["estado_validacao", "atualizado_em"])
            if backup.estado_recebimento != "RECEBIDO":
                backup.estado_recebimento = "FALHA"
                backup.save(update_fields=["estado_recebimento", "atualizado_em"])
    except Exception:
        # A falha original continua funcional mesmo se o banco estiver indisponivel.
        pass
    logger.warning("Hub backup failure code=%s upload=%s", error.code, upload.upload_uuid)


@contextlib.contextmanager
def _attempt(upload):
    try:
        yield
    except Exception as exc:
        from cryptography.exceptions import InvalidTag
        if isinstance(exc, RecebimentoErro):
            error = exc
        elif isinstance(exc, BackupIOError):
            error = RecebimentoErro(exc.code, exc.status)
        elif isinstance(exc, (InvalidTag, ValueError)):
            error = RecebimentoErro("ENCRYPTED_FILE_INVALID", 409, True)
        elif isinstance(exc, (TimeoutError, ConnectionError)):
            error = RecebimentoErro("REQUEST_TIMEOUT", 408)
        elif isinstance(exc, OSError):
            error = RecebimentoErro("INSUFFICIENT_SPACE" if exc.errno == 28 else "STORAGE_UNAVAILABLE", 507 if exc.errno == 28 else 503)
        elif isinstance(exc, ImproperlyConfigured):
            error = RecebimentoErro("RECEIVING_CONFIGURATION", 503)
        elif isinstance(exc, ValidationError):
            error = RecebimentoErro("MANIFEST_OR_CHAIN_INVALID", 400)
        elif isinstance(exc, OperationalError):
            error = RecebimentoErro("DATABASE_UNAVAILABLE", 503)
        else:
            raise
        _failure(upload, error)
        raise error from None


def _initialize_file(upload, raw, keys):
    stage, final = stage_path(upload), destination(upload.backup)
    if final.exists():
        _read_manifest(upload, keys, final)
        return
    if stage.exists():
        previous, offset = _read_manifest(upload, keys, stage)
        if previous != raw:
            raise RecebimentoErro("MANIFEST_CONFLICT", 409, True)
        if upload.offset_arquivo:
            if stage.stat().st_size < upload.offset_arquivo:
                raise RecebimentoErro("STAGE_TRUNCATED", 409, True)
            return
    else:
        if upload.offset_arquivo or upload.bytes_recebidos:
            raise RecebimentoErro("STAGE_MISSING", 409, True)
        _space(len(raw) + 8192)
        header = header_upload(upload)
        with open_private(stage, create=True, writable=True) as stream:
            escrever_header(stream, header)
            escrever_registro(stream, cipher_upload(upload, keys), header, -1, raw)
            stream.flush(); os.fsync(stream.fileno())
            offset = stream.tell()
    with transaction.atomic():
        upload.offset_arquivo = offset
        upload.save(update_fields=["offset_arquivo", "atualizado_em"])


def iniciar(hub, raw):
    keys = configuracao_recebimento()
    current = _hub(hub)
    try:
        if isinstance(raw, str) and len(raw.encode("utf-8")) > settings.HUB_BACKUP_MAX_MANIFEST_BYTES:
            raise RecebimentoErro("MANIFEST_TOO_LARGE", 413)
    except UnicodeError:
        raise ValidationError("Manifesto deve ser UTF-8 valido.") from None
    metadata, doc, encoded = ler_manifesto(raw, current, settings.HUB_BACKUP_MAX_MANIFEST_BYTES)
    if metadata["tamanho_bytes"] > settings.HUB_BACKUP_MAX_FILE_BYTES or (metadata["tamanho_bytes"] + settings.HUB_BACKUP_PART_BYTES - 1) // settings.HUB_BACKUP_PART_BYTES > 1000000:
        raise RecebimentoErro("FILE_TOO_LARGE", 413)
    _check_predecessor(doc, metadata, keys)
    backup, _ = registrar_metadados_backup(hub=current, **metadata)
    with backup_lock(backup):
        with transaction.atomic():
            current = _hub(hub, lock=True)
            # Revalidacao sob trava tambem protege contra mudanca da instalacao.
            backup = _backup(current, backup.backup_uuid)
            upload = backup.uploads.select_related("backup").first()
            if backup.estado_recebimento == "RECEBIDO":
                if backup.estado_validacao in ("INVALIDO", "CADEIA_INTERROMPIDA"):
                    raise RecebimentoErro("SUPPORT_REQUIRED", 409)
                if not upload or upload.estado != "RECEBIDO":
                    raise RecebimentoErro("SUPPORT_REQUIRED", 409)
            elif upload is None or upload.estado == "ERRO":
                upload = HubBackupUpload.objects.create(
                    backup=backup, tentativa=(upload.tentativa + 1 if upload else 1),
                    chave_id=settings.HUB_BACKUP_ACTIVE_KEY_ID, sal=base64.b64encode(os.urandom(32)).decode("ascii"),
                    tamanho_parte=settings.HUB_BACKUP_PART_BYTES, manifesto_tamanho=len(encoded),
                    expira_em=timezone.now() + timedelta(seconds=settings.HUB_BACKUP_IDLE_SECONDS),
                )
            else:
                upload.expira_em = timezone.now() + timedelta(seconds=settings.HUB_BACKUP_IDLE_SECONDS)
                upload.codigo_erro = ""
                upload.save(update_fields=["expira_em", "codigo_erro", "atualizado_em"])
            if backup.estado_recebimento == "FALHA" and upload.estado == "ABERTO":
                backup.estado_recebimento = "PENDENTE"
                backup.save(update_fields=["estado_recebimento", "atualizado_em"])
        with _attempt(upload):
            _initialize_file(upload, encoded, keys)
        return _progress(upload)


def progresso(hub, backup_uuid):
    configuracao_recebimento()
    return _progress(_latest(_backup(hub, backup_uuid)))


def enviar_parte(hub, backup_uuid, upload_uuid, indice, offset, digest, stream, content_length):
    keys = configuracao_recebimento()
    backup = _backup(hub, backup_uuid)
    if not upload_uuid:
        raise RecebimentoErro("UPLOAD_ID_REQUIRED", 400)
    with backup_lock(backup):
        upload = _latest(backup, upload_uuid)
        with _attempt(upload):
            if upload.estado not in ("ABERTO", "RECEBIDO"):
                raise RecebimentoErro("UPLOAD_CLOSED", 409)
            if upload.estado == "ABERTO" and upload.expira_em <= timezone.now():
                raise RecebimentoErro("UPLOAD_TIMEOUT", 408)
            if type(indice) is not int or type(offset) is not int or indice < 0 or offset < 0 or offset != indice * upload.tamanho_parte:
                raise RecebimentoErro("PART_OUT_OF_ORDER", 409)
            expected_size = min(upload.tamanho_parte, backup.tamanho_bytes - offset)
            if type(content_length) is not int or content_length <= 0 or content_length > settings.HUB_BACKUP_PART_BYTES or content_length > 16 * 1024**2:
                raise RecebimentoErro("PART_TOO_LARGE", 413)
            if content_length != expected_size or not re.fullmatch(r"[a-f0-9]{64}", digest or ""):
                raise RecebimentoErro("PART_SIZE_OR_HASH_INVALID", 400)
            if indice > upload.partes_recebidas:
                raise RecebimentoErro("PART_OUT_OF_ORDER", 409)
            data = stream.read(content_length + 1)
            if len(data) != content_length or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest):
                raise RecebimentoErro("PART_CHECKSUM_MISMATCH", 422)
            if indice < upload.partes_recebidas:
                part = upload.partes.filter(indice=indice).first()
                if not part or (part.offset, part.tamanho, part.sha256) != (offset, len(data), digest):
                    raise RecebimentoErro("PART_CONFLICT", 409)
                return _progress(upload)
            if upload.estado != "ABERTO" or destination(backup).exists():
                raise RecebimentoErro("UPLOAD_CLOSED", 409)
            stage = stage_path(upload)
            if not stage.exists() or not upload.offset_arquivo:
                raise RecebimentoErro("STAGE_MISSING", 409, True)
            _space(len(data) + 64)
            with open_private(stage, writable=True) as target:
                if os.fstat(target.fileno()).st_size < upload.offset_arquivo:
                    raise RecebimentoErro("STAGE_TRUNCATED", 409, True)
                # Cauda de uma gravacao interrompida nunca e checkpoint confirmado.
                target.truncate(upload.offset_arquivo); target.seek(upload.offset_arquivo)
                escrever_registro(target, cipher_upload(upload, keys), header_upload(upload), indice, data)
                target.flush(); os.fsync(target.fileno())
                end = target.tell()
            with transaction.atomic():
                _hub(hub, lock=True)
                HubBackupParte.objects.create(upload=upload, indice=indice, offset=offset, tamanho=len(data), sha256=digest,
                                             offset_arquivo=upload.offset_arquivo, fim_arquivo=end)
                upload.bytes_recebidos += len(data); upload.partes_recebidas += 1; upload.offset_arquivo = end
                upload.codigo_erro = ""; upload.expira_em = timezone.now() + timedelta(seconds=settings.HUB_BACKUP_IDLE_SECONDS)
                upload.save(update_fields=["bytes_recebidos", "partes_recebidas", "offset_arquivo", "codigo_erro", "expira_em", "atualizado_em"])
            return _progress(upload)


def _parts_bounded(upload):
    # MySQLdb armazena o resultado SQL no cliente: LIMIT evita carregar todos os
    # metadados mesmo nesse backend (iterator sozinho nao garante streaming nele).
    next_index = 0
    while True:
        batch = list(upload.partes.filter(indice__gte=next_index).order_by("indice")[:1000])
        if not batch:
            return
        yield from batch
        next_index = batch[-1].indice + 1


def iter_verified_file(upload, keys, path):
    header, digest, count, total = header_upload(upload), hashlib.sha256(), 0, 0
    deadline = time.monotonic() + settings.HUB_BACKUP_FINALIZE_SECONDS
    cipher = cipher_upload(upload, keys)
    with open_private(path) as stream:
        conferir_header(stream, header)
        manifest = ler_registro(stream, cipher, header, -1, upload.manifesto_tamanho)
        if hashlib.sha256(manifest).hexdigest() != upload.backup.manifesto_sha256:
            raise RecebimentoErro("MANIFEST_CONFLICT", 409, True)
        yield manifest
        last = None
        for part in _parts_bounded(upload):
            if time.monotonic() > deadline:
                raise RecebimentoErro("FINALIZE_TIMEOUT", 408)
            expected_size = min(upload.tamanho_parte, upload.backup.tamanho_bytes - total)
            if (part.indice, part.offset, part.offset_arquivo, part.tamanho) != (count, total, stream.tell(), expected_size):
                raise RecebimentoErro("PART_CHAIN_INVALID", 409, True)
            data = ler_registro(stream, cipher, header, count, part.tamanho)
            if stream.tell() != part.fim_arquivo or hashlib.sha256(data).hexdigest() != part.sha256:
                raise RecebimentoErro("PART_CHECKSUM_MISMATCH", 422, True)
            digest.update(data); total += len(data); count += 1
            if total == upload.backup.tamanho_bytes:
                last = data  # Withhold final bytes until global SHA and EOF are checked.
            else:
                yield data
        if stream.read(1) or count != upload.partes_recebidas or total != upload.backup.tamanho_bytes or total != upload.bytes_recebidos or stream.tell() != upload.offset_arquivo:
            raise RecebimentoErro("FILE_INCOMPLETE", 409, True)
    if not hmac.compare_digest(digest.hexdigest(), upload.backup.sha256):
        raise RecebimentoErro("FILE_CHECKSUM_MISMATCH", 422, True)
    if last is not None:
        yield last


def _verify_file(upload, keys, path):
    # Same verifier for receipt, independent integrity and recovery streaming.
    with contextlib.closing(iter_verified_file(upload, keys, path)) as records:
        manifest = next(records)
        for _ in records:
            pass
    return manifest


def finalizar(hub, backup_uuid, upload_uuid):
    keys = configuracao_recebimento()
    backup = _backup(hub, backup_uuid)
    if not upload_uuid:
        raise RecebimentoErro("UPLOAD_ID_REQUIRED", 400)
    with backup_lock(backup):
        upload = _latest(backup, upload_uuid)
        with _attempt(upload):
            if upload.estado == "ERRO":
                raise RecebimentoErro("UPLOAD_FAILED", 409)
            if upload.estado != "RECEBIDO" and upload.expira_em <= timezone.now():
                raise RecebimentoErro("UPLOAD_TIMEOUT", 408)
            if upload.bytes_recebidos != backup.tamanho_bytes:
                raise RecebimentoErro("FILE_INCOMPLETE", 409)
            final, stage = destination(backup), stage_path(upload)
            if not final.exists():
                if not stage.exists():
                    raise RecebimentoErro("STAGE_MISSING", 409, True)
                # Se houve crash apos write e antes do checkpoint, descartar apenas a cauda temporaria.
                with open_private(stage, writable=True) as target:
                    if target.seek(0, 2) < upload.offset_arquivo:
                        raise RecebimentoErro("STAGE_TRUNCATED", 409, True)
                    target.truncate(upload.offset_arquivo); target.flush(); os.fsync(target.fileno())
            raw = _verify_file(upload, keys, final if final.exists() else stage)
            metadata, doc, _ = ler_manifesto(raw.decode("utf-8"), _hub(hub), settings.HUB_BACKUP_MAX_MANIFEST_BYTES)
            _check_predecessor(doc, metadata, keys)
            with transaction.atomic():
                _hub(hub, lock=True)
                backup = _backup(hub, backup_uuid)
                if not final.exists():
                    publish(upload)
                # Arquivo recebido != backup validado para recuperacao. Nao aprovar cadeia aqui.
                if backup.estado_recebimento != "RECEBIDO":
                    backup.estado_recebimento = "RECEBIDO"; backup.recebido_em = timezone.now()
                    backup.save(update_fields=["estado_recebimento", "recebido_em", "atualizado_em"])
                upload.estado = "RECEBIDO"; upload.codigo_erro = ""
                upload.save(update_fields=["estado", "codigo_erro", "atualizado_em"])
                upload.backup = backup
            return _progress(upload)

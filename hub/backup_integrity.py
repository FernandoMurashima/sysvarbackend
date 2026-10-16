"""Independent integrity audit and conservative retention plan. Never deletes files."""
from collections import defaultdict
from copy import copy
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.exceptions import InvalidTag
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from .backup_crypto import configuracao_criptografia
from .backup_files import BackupIOError, backup_lock, destination, sem_links
from .backup_manifest import ler_manifesto
from .backup_uploads import _verify_file
from .models import HubBackup, HubBackupUpload, SysvarHub


class IntegrityError(Exception):
    """Safe diagnostic code, never native exception text or backup contents."""


def retention_timezone():
    try:
        return ZoneInfo(settings.HUB_BACKUP_RETENTION_TIME_ZONE)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ImproperlyConfigured("Fuso de retencao de backups invalido.") from None


def _scope(empresa_id, loja_id, hub_id):
    hub = SysvarHub.objects.select_related("loja").filter(
        pk=hub_id, loja_id=loja_id, loja__empresa_id=empresa_id).first()
    if not hub:
        raise ValidationError("Hub, loja e empresa nao correspondem ao mesmo escopo.")
    return hub


def _rows(hub):
    return list(HubBackup.objects.do_hub(hub).order_by("tipo", "sequencia", "pk"))


def _fingerprint(backup):
    return tuple(getattr(backup, field) for field in (*HubBackup.METADADOS,
        "estado_recebimento", "estado_validacao", "recebido_em", "verificado_em", "atualizado_em"))


def _file_identity(path):
    sem_links(path)
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _upload_fingerprint(upload):
    return tuple(getattr(upload, field.attname) for field in upload._meta.concrete_fields)


def _verify(backup, hub, keys):
    uploads = list(HubBackupUpload.objects.filter(backup_id=backup.pk).order_by("-tentativa")[:2])
    if not uploads or uploads[0].estado != "RECEBIDO" or backup.recebido_em is None:
        raise IntegrityError("RECEIPT_UNCONFIRMED")
    upload = uploads[0]
    if not 1 <= upload.tamanho_parte <= 16 * 1024**2 or not 1 <= upload.manifesto_tamanho <= 1024**2:
        raise IntegrityError("UPLOAD_METADATA_INVALID")
    with backup_lock(backup, existing_only=True):
        path = destination(backup)
        before = _file_identity(path)
        raw = _verify_file(upload, keys, path)  # AES-GCM + bounded parts + global SHA + EOF.
        # Historical installations remain verifiable after a new pairing. The
        # installation context comes from immutable Central metadata, not the manifest.
        context = copy(hub)
        context.hub_uuid = backup.instalacao_uuid
        metadata, doc, _ = ler_manifesto(raw.decode("utf-8"), context, 1024**2, verificar_estados=False)
        candidate = HubBackup(hub=context, empresa_id=hub.loja.empresa_id, loja_id=hub.loja_id, **metadata)
        candidate.preparar_metadados(context)
        if any(getattr(candidate, field) != getattr(backup, field) for field in HubBackup.METADADOS):
            raise IntegrityError("MANIFEST_METADATA_CONFLICT")
        if before != _file_identity(path):
            raise IntegrityError("CONCURRENT_FILE_CHANGE")
    return doc, (path, before), (upload.pk, _upload_fingerprint(upload))


def _relations(backup, rows):
    if backup.tipo == HubBackup.FULL:
        return None, None
    full, previous = rows.get(backup.full_referencia_id), rows.get(backup.predecessor_id)
    if not full or not previous or full.tipo != HubBackup.FULL:
        raise IntegrityError("CHAIN_MISSING")
    if (backup.sequencia < 1 or (backup.sequencia == 1 and previous.pk != full.pk) or
            (backup.sequencia > 1 and (previous.tipo != HubBackup.INCREMENTAL or
             previous.full_referencia_id != full.pk or previous.sequencia + 1 != backup.sequencia))):
        raise IntegrityError("CHAIN_SEQUENCE")
    identity = ("hub_id", "empresa_id", "loja_id", "instalacao_uuid", "servidor_mysql_uuid")
    if any(getattr(backup, field) != getattr(parent, field) for parent in (full, previous) for field in identity):
        raise IntegrityError("CHAIN_IDENTITY")
    return full, previous


def _chain(backup, document, rows, documents, results):
    full, previous = _relations(backup, rows)
    if full is None:
        return
    if results.get(full.pk, {}).get("integridade") != "VALIDO" or results.get(previous.pk, {}).get("integridade") != "VALIDO":
        raise IntegrityError("PREDECESSOR_UNVERIFIED")
    prior_point = documents[previous.pk]
    if (document["start"]["anchor"] != prior_point["anchor"] or document["previous_manifest_sha256"] != previous.manifesto_sha256
            or (backup.binlog_inicio_arquivo, backup.binlog_inicio_posicao) != (prior_point["file"], prior_point["position"])):
        raise IntegrityError("CHAIN_ANCHOR_CONFLICT")


def _result(backup, integrity, code):
    return dict(backup_uuid=str(backup.backup_uuid), tipo=backup.tipo, sequencia=backup.sequencia,
                gerado_em=backup.gerado_em.isoformat(), chave_armazenamento=backup.chave_armazenamento,
                estado_recebimento=backup.estado_recebimento, estado_validacao=backup.estado_validacao,
                integridade=integrity, codigo=code)


def _assert_snapshot(hub, rows, files, uploads, *, selected=False):
    current = (list(HubBackup.objects.do_hub(hub).filter(pk__in=[b.pk for b in rows]))
               if selected else _rows(hub))
    if {row.pk: _fingerprint(row) for row in current} != {row.pk: _fingerprint(row) for row in rows}:
        raise IntegrityError("CONCURRENT_METADATA_CHANGE")
    for path, fingerprint in files.values():
        if _file_identity(path) != fingerprint:
            raise IntegrityError("CONCURRENT_FILE_CHANGE")
    for upload_id, fingerprint in uploads.values():
        if _upload_fingerprint(HubBackupUpload.objects.get(pk=upload_id)) != fingerprint:
            raise IntegrityError("CONCURRENT_UPLOAD_CHANGE")


def _persist(hub, rows, results, files, uploads):
    # No transaction is kept open while decrypting large files. Recheck the snapshot
    # under the same Hub lock used by receipt commits before changing any state.
    with transaction.atomic():
        current_hub = SysvarHub.objects.select_for_update().select_related("loja").get(pk=hub.pk)
        if (current_hub.loja_id, current_hub.loja.empresa_id, current_hub.hub_uuid) != (hub.loja_id, hub.loja.empresa_id, hub.hub_uuid):
            raise IntegrityError("CONCURRENT_SCOPE_CHANGE")
        _assert_snapshot(current_hub, rows, files, uploads)
        for backup in rows:
            if backup.estado_recebimento != HubBackup.RECEBIDO:
                continue
            result = results[backup.pk]
            backup.estado_validacao = (HubBackup.VALIDO if result["integridade"] == "VALIDO" else
                HubBackup.INVALIDO if result["integridade"] == "INVALIDO" else HubBackup.CADEIA_INTERROMPIDA)
            backup.verificado_em = timezone.now()
            backup.save(update_fields=["estado_validacao", "verificado_em", "atualizado_em"])
            result["estado_validacao"] = backup.estado_validacao


def _retention(rows, results, now, tz):
    cutoff = now.astimezone(tz).date() - timedelta(days=1)
    groups, reasons = defaultdict(list), defaultdict(set)
    for backup in rows:
        groups[backup.pk if backup.tipo == HubBackup.FULL else backup.full_referencia_id].append(backup)
    healthy, blocked = defaultdict(list), {}
    for group_id, group in groups.items():
        blocked[group_id] = any(results[b.pk]["integridade"] != "VALIDO" for b in group)
        by_pk = {b.pk: b for b in group}
        doubtful = any(results[b.pk]["integridade"] != "VALIDO" or b.estado_recebimento != HubBackup.RECEBIDO
                       or b.estado_validacao != HubBackup.VALIDO for b in group)
        if doubtful:
            for b in group:
                reasons[b.pk].add("CADEIA_PENDENTE_OU_COM_DUVIDA")
        else:
            root = next((b for b in group if b.tipo == HubBackup.FULL), None)
            if root:
                healthy[(root.instalacao_uuid, root.servidor_mysql_uuid)].append(group)
        for point in group:
            if point.gerado_em.astimezone(tz).date() >= cutoff:
                # Dates in the future are also preserved; clock anomalies cannot expire files.
                current, seen = point, set()
                while current and current.pk not in seen:
                    seen.add(current.pk)
                    if "DIA_ATUAL_ANTERIOR_OU_DEPENDENCIA" in reasons[current.pk]:
                        break
                    reasons[current.pk].add("DIA_ATUAL_ANTERIOR_OU_DEPENDENCIA")
                    current = by_pk.get(current.predecessor_id)
    for candidates in healthy.values():
        latest = max(candidates, key=lambda group: max((b.gerado_em, b.pk) for b in group))
        for b in latest:
            reasons[b.pk].add("ULTIMA_CADEIA_INTEGRA_DA_INSTALACAO_MYSQL")
    # Only independent whole chains may be eligible. A clock reversal in a later
    # incremental must not turn a suffix of a protected chain into a removal target.
    for group in groups.values():
        if any(reasons[b.pk] for b in group):
            for backup in group:
                if not reasons[backup.pk]:
                    reasons[backup.pk].add("CADEIA_CONTEM_PONTO_PROTEGIDO")
    for backup in rows:
        result = results[backup.pk]
        keep = reasons[backup.pk]
        result["retencao"] = "MANTER" if keep else "ELEGIVEL_REMOCAO"
        result["motivos"] = sorted(keep) if keep else ["CADEIA_ANTIGA_FORA_DA_JANELA_COM_ALTERNATIVA_INTEGRA"]
        result["cadeia_bloqueada"] = blocked[backup.pk if backup.tipo == HubBackup.FULL else backup.full_referencia_id]


def auditar_backups_hub(*, empresa_id, loja_id, hub_id, registrar=False, agora=None):
    """Dry-run by default. registrar is reserved for explicitly authorized internal use.

    The CLI never sets registrar. Retention is always a plan, with no deletion API.
    """
    hub = _scope(empresa_id, loja_id, hub_id)
    tz, now = retention_timezone(), agora or timezone.now()
    if timezone.is_naive(now):
        raise ValidationError("Data de referencia deve incluir fuso horario.")
    rows = _rows(hub)
    by_pk, documents, results, files, uploads = {b.pk: b for b in rows}, {}, {}, {}, {}
    try:
        keys = configuracao_criptografia()
        key_error = False
    except ImproperlyConfigured:
        keys, key_error = {}, True
    for backup in rows:
        if backup.estado_recebimento != HubBackup.RECEBIDO:
            results[backup.pk] = _result(backup, "PENDENTE", "RECEIPT_INCOMPLETE")
            continue
        try:
            if key_error:
                raise ImproperlyConfigured()
            # Resolve only scoped parents before any path helper can dereference a FULL.
            _relations(backup, by_pk)
            document, file_identity, upload_identity = _verify(backup, hub, keys)
            # Keep physical proof even if a subsequent chain check fails.
            files[backup.pk], uploads[backup.pk] = file_identity, upload_identity
            _chain(backup, document, by_pk, documents, results)
            documents[backup.pk] = document["binlog"] if backup.tipo == HubBackup.FULL else document["end"]
            results[backup.pk] = _result(backup, "VALIDO", "INTEGRITY_AND_CHAIN_VERIFIED")
        except ImproperlyConfigured:
            results[backup.pk] = _result(backup, "INCONCLUSIVO", "KEY_OR_STORAGE_UNAVAILABLE")
        except (InvalidTag, ValueError, UnicodeError, ValidationError):
            results[backup.pk] = _result(backup, "INVALIDO", "CONTAINER_MANIFEST_OR_METADATA_INVALID")
        except IntegrityError as error:
            results[backup.pk] = _result(backup, "CADEIA_INTERROMPIDA", str(error))
        except BackupIOError as error:
            terminal = getattr(error, "terminal", False)
            results[backup.pk] = _result(backup, "INVALIDO" if terminal else "INCONCLUSIVO", error.code)
        except FileNotFoundError:
            results[backup.pk] = _result(backup, "CADEIA_INTERROMPIDA", "FILE_OR_LOCK_MISSING")
        except OSError:
            results[backup.pk] = _result(backup, "INCONCLUSIVO", "STORAGE_UNAVAILABLE")
    if registrar:
        _persist(hub, rows, results, files, uploads)
    else:
        try:
            current_hub = _scope(empresa_id, loja_id, hub_id)
            if current_hub.hub_uuid != hub.hub_uuid:
                raise IntegrityError("CONCURRENT_SCOPE_CHANGE")
            _assert_snapshot(current_hub, rows, files, uploads)
        except (IntegrityError, OSError, BackupIOError):
            for result in results.values():
                result.update(integridade="INCONCLUSIVO", codigo="SNAPSHOT_CHANGED_RETRY")
    _retention(rows, results, now, tz)
    return dict(dry_run=not registrar, retencao_dry_run=True, restauracao_homologada=False,
                empresa_id=hub.loja.empresa_id, loja_id=hub.loja_id, hub_id=hub.pk,
                fuso=tz.key, referencia=now.isoformat(), backups=list(results.values()))


def verificar_ponto_backup(hub, rows):
    """Fresh physical proof of exactly the selected ancestry; never promotes RECEBIDO."""
    keys = configuracao_criptografia()
    by_pk = {b.pk: b for b in rows}
    points, starts, results, files, uploads = {}, {}, {}, {}, {}
    for backup in rows:
        if (backup.hub_id != hub.pk or backup.loja_id != hub.loja_id or backup.empresa_id != hub.loja.empresa_id
                or backup.estado_recebimento != HubBackup.RECEBIDO or backup.estado_validacao != HubBackup.VALIDO
                or backup.verificado_em is None):
            raise IntegrityError("VALIDATED_CHAIN_REQUIRED")
        _relations(backup, by_pk)
        document, file_identity, upload_identity = _verify(backup, hub, keys)
        _chain(backup, document, by_pk, points, results)
        starts[backup.pk] = document["binlog"] if backup.tipo == HubBackup.FULL else document["start"]
        points[backup.pk] = document["binlog"] if backup.tipo == HubBackup.FULL else document["end"]
        results[backup.pk] = {"integridade": "VALIDO"}
        files[backup.pk], uploads[backup.pk] = file_identity, upload_identity
    _assert_snapshot(hub, rows, files, uploads, selected=True)
    return dict(points=points, starts=starts, files=files, uploads=uploads)

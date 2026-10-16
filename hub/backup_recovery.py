"""Private, operator-authorized recovery packages. No SQL execution or cleartext staging."""
import hashlib
import json
from contextlib import ExitStack
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied, NotFound

from accounts.services.effective_access import EffectiveAccessService
from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService
from .backup_crypto import configuracao_criptografia
from .backup_files import backup_lock, storage_root
from .backup_integrity import _file_identity, _relations, verificar_ponto_backup
from .backup_uploads import iter_verified_file
from .models import HubBackup, HubBackupUpload, SysvarHub

SALT = "sysvar.hub.backup.recovery.v1"


class RecoveryError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def recovery_configuration():
    if not settings.HUB_BACKUP_RECOVERY_ATIVO:
        raise RecoveryError("RECOVERY_DISABLED", 503)
    seconds = settings.HUB_BACKUP_RECOVERY_AUTH_SECONDS
    if type(seconds) is not int or not 60 <= seconds <= 86400 or len(settings.SECRET_KEY) < 32 or settings.SECRET_KEY == "dev-insecure-key-change-me":
        raise ImproperlyConfigured("Configuracao privada de recuperacao invalida.")
    configuracao_criptografia()
    storage_root()


def support_scope(user, empresa_id, loja_id, *, write=False):
    from .views import _validar_usuario_admin_hub, _loja_no_escopo_usuario
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied()
    # Resolve persisted access anew; do not reuse cached permissions from old grants.
    user = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if not user:
        raise PermissionDenied()
    _validar_usuario_admin_hub(user)
    if not user.has_perm("hub.view_hubbackup") or (write and not user.has_perm("hub.change_hubbackup")):
        raise PermissionDenied("Permissao administrativa de backup obrigatoria.")
    loja = _loja_no_escopo_usuario(user, loja_id)
    if loja.empresa_id != empresa_id:
        raise NotFound()
    hub = SysvarHub.objects.select_related("loja", "loja__empresa").filter(loja_id=loja.pk).first()
    if not hub or not EffectiveAccessService(user).can_access_store(hub.loja):
        raise NotFound()
    return user, hub


def metadata_chain(hub, point_uuid, installation, server):
    scope = HubBackup.objects.do_hub(hub).filter(instalacao_uuid=installation, servidor_mysql_uuid=server)
    current = scope.filter(backup_uuid=point_uuid).first()
    if not current:
        raise NotFound()
    chain, seen = [], set()
    while current:
        if current.pk in seen:
            raise RecoveryError("CHAIN_CYCLE")
        seen.add(current.pk); chain.append(current)
        if current.tipo == HubBackup.FULL:
            break
        current = scope.filter(pk=current.predecessor_id).first()
    if not current or chain[-1].tipo != HubBackup.FULL:
        raise RecoveryError("CHAIN_INCOMPLETE")
    chain.reverse()
    by_pk = {b.pk: b for b in chain}
    for backup in chain:
        _relations(backup, by_pk)
        if (backup.estado_recebimento != "RECEBIDO" or backup.estado_validacao != "VALIDO"
                or backup.recebido_em is None or backup.verificado_em is None):
            raise RecoveryError("VALIDATED_CHAIN_REQUIRED")
    return chain


def package_manifest(hub, rows, proof, package_id, created):
    files = []
    for order, b in enumerate(rows):
        files.append(dict(ordem=order, backup_uuid=str(b.backup_uuid), tipo=b.tipo, sequencia=b.sequencia,
            nome=f"{order:06d}-{b.tipo.lower()}-{b.backup_uuid.hex}.sql.gz", tamanho=b.tamanho_bytes,
            sha256=b.sha256, manifesto_origem_sha256=b.manifesto_sha256,
            gerado_em=b.gerado_em.isoformat(), inicio=proof["starts"][b.pk],
            fim=None if b.tipo == "FULL" else proof["points"][b.pk],
            predecessor_uuid=None if order == 0 else str(rows[order-1].backup_uuid),
            formato=b.formato, compressao=b.compressao,
            download_url=reverse("hub-backup-recovery-file", kwargs=dict(empresa_id=hub.loja.empresa_id,
                                loja_id=hub.loja_id, backup_uuid=b.backup_uuid))))
    return dict(versao=1, pacote_uuid=package_id, criado_em=created, empresa_id=hub.loja.empresa_id,
        loja_id=hub.loja_id, hub_id=hub.pk, instalacao_uuid=str(rows[0].instalacao_uuid),
        servidor_mysql_uuid=str(rows[0].servidor_mysql_uuid), full_uuid=str(rows[0].backup_uuid),
        ponto_uuid=str(rows[-1].backup_uuid), ponto_gerado_em=rows[-1].gerado_em.isoformat(),
        integridade="VALIDO_REVALIDADO", restauracao_homologada=False, arquivos=files)


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _audit(action, user, hub, request, metadata, *, required=True):
    method = AuditService.required_success if required else AuditService.success
    event = method(action, category=AuditCategory.SECURITY, user=user, request=request,
        empresa=hub.loja.empresa, loja=hub.loja, app_label="hub", model="hubbackup", metadata=metadata)
    if required and event is None:
        raise RecoveryError("AUDIT_UNAVAILABLE", 503)


def authorize_package(user, hub, payload, request):
    if set(payload) != {"ponto_uuid", "instalacao_uuid", "servidor_mysql_uuid", "autorizar", "motivo"} or payload["autorizar"] is not True:
        raise RecoveryError("EXPLICIT_AUTHORIZATION_REQUIRED", 400)
    if not isinstance(payload["motivo"], str) or not 1 <= len(payload["motivo"].strip()) <= 500:
        raise RecoveryError("SUPPORT_REASON_REQUIRED", 400)
    try:
        point, installation, server = (UUID(payload[key]) for key in ("ponto_uuid", "instalacao_uuid", "servidor_mysql_uuid"))
    except (ValueError, TypeError, AttributeError):
        raise RecoveryError("INVALID_IDENTITY", 400) from None
    rows = metadata_chain(hub, point, installation, server)
    proof = verificar_ponto_backup(hub, rows)
    package_id, created = str(uuid4()), timezone.now().isoformat()
    manifest = package_manifest(hub, rows, proof, package_id, created)
    digest = manifest_hash(manifest)
    claims = dict(version=1, user_id=user.pk, empresa_id=hub.loja.empresa_id, loja_id=hub.loja_id, hub_id=hub.pk,
                  point=str(point), installation=str(installation), server=str(server), package_id=package_id,
                  created=created, manifest_sha256=digest)
    _audit(AuditAction.HUB_BACKUP_RECOVERY_AUTHORIZED, user, hub, request,
           dict(pacote_uuid=package_id, ponto_uuid=str(point), manifesto_sha256=digest,
                motivo_sha256=hashlib.sha256(payload["motivo"].encode()).hexdigest()))
    return dict(manifesto=manifest, manifesto_sha256=digest,
                autorizacao_token=signing.dumps(claims, salt=SALT), validade_segundos=settings.HUB_BACKUP_RECOVERY_AUTH_SECONDS)


def load_authorization(token, user, hub):
    if not isinstance(token, str) or not 1 <= len(token) <= 8192:
        raise RecoveryError("RECOVERY_AUTHORIZATION_REQUIRED", 403)
    try:
        claims = signing.loads(token, salt=SALT, max_age=settings.HUB_BACKUP_RECOVERY_AUTH_SECONDS)
        if (claims["version"] != 1 or claims["user_id"] != user.pk or claims["hub_id"] != hub.pk
                or claims["empresa_id"] != hub.loja.empresa_id or claims["loja_id"] != hub.loja_id):
            raise ValueError()
        for name in ("point", "installation", "server", "package_id"):
            UUID(claims[name])
        return claims
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise RecoveryError("RECOVERY_AUTHORIZATION_INVALID", 403) from None


class RecoveryStream:
    def __init__(self, resources, records, user, hub, request, package_id, backup, path, fingerprint):
        self.resources, self.records = resources, records
        self.user, self.hub, self.request = user, hub, request
        self.package_id, self.backup = package_id, backup
        self.path, self.fingerprint = path, fingerprint
        self.closed, self.completed, self.sent = False, False, 0

    def __iter__(self):
        try:
            for part in self.records:
                if _file_identity(self.path) != self.fingerprint:
                    raise RecoveryError("CONCURRENT_FILE_CHANGE")
                for offset in range(0, len(part), 65536):
                    if self.closed:
                        return
                    data = part[offset:offset+65536]
                    self.sent += len(data)
                    yield data
            if _file_identity(self.path) != self.fingerprint:
                raise RecoveryError("CONCURRENT_FILE_CHANGE")
            self.completed = True
        except GeneratorExit:
            raise
        except Exception:
            # Stream failures contain no native error, SQL or crypto material.
            raise RecoveryError("RECOVERY_STREAM_INTERRUPTED", 503) from None
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.resources.close()
        try:
            _audit(AuditAction.HUB_BACKUP_RECOVERY_COMPLETED if self.completed else AuditAction.HUB_BACKUP_RECOVERY_INTERRUPTED,
                self.user, self.hub, self.request,
                dict(pacote_uuid=self.package_id, backup_uuid=str(self.backup.backup_uuid), bytes_enviados=self.sent), required=False)
        except Exception:
            pass


def download_file(user, hub, token, backup_uuid, request):
    claims = load_authorization(token, user, hub)
    rows = metadata_chain(hub, claims["point"], claims["installation"], claims["server"])
    backup = next((b for b in rows if b.backup_uuid == backup_uuid), None)
    if not backup:
        raise NotFound()
    proof = verificar_ponto_backup(hub, rows)
    manifest = package_manifest(hub, rows, proof, claims["package_id"], claims["created"])
    if manifest_hash(manifest) != claims["manifest_sha256"]:
        raise RecoveryError("PACKAGE_CONFLICT")
    # Long physical checks cannot extend an expired admission authorization.
    load_authorization(token, user, hub)
    resources = ExitStack()
    try:
        resources.enter_context(backup_lock(backup, existing_only=True))
        path, fingerprint = proof["files"][backup.pk]
        if _file_identity(path) != fingerprint:
            raise RecoveryError("CONCURRENT_FILE_CHANGE")
        upload_id, _ = proof["uploads"][backup.pk]
        upload = HubBackupUpload.objects.select_related("backup").get(pk=upload_id, backup_id=backup.pk)
        records = iter_verified_file(upload, configuracao_criptografia(), path)
        resources.callback(records.close)
        raw = next(records)
        if hashlib.sha256(raw).hexdigest() != backup.manifesto_sha256:
            raise RecoveryError("MANIFEST_CONFLICT")
        _audit(AuditAction.HUB_BACKUP_RECOVERY_DOWNLOAD, user, hub, request,
               dict(pacote_uuid=claims["package_id"], backup_uuid=str(backup.backup_uuid)))
        stream = RecoveryStream(resources, records, user, hub, request, claims["package_id"], backup, path, fingerprint)
        return stream, next(item for item in manifest["arquivos"] if item["backup_uuid"] == str(backup_uuid)), claims["package_id"]
    except Exception:
        resources.close()
        raise

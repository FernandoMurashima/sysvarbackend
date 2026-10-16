"""Le o manifesto original UTF-8; hashes de predecessor sao dos bytes originais."""
import hashlib
import json
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime

from .models import HubBackup


def json_sem_duplicatas(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError
    return value


def _integer(value, minimum=1, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError
    return value


def _coordinate(value):
    name = value["file"]
    if not isinstance(name, str) or len(name) > 255 or not re.fullmatch(r"[A-Za-z0-9_.-]+\.[0-9]{6,}", name):
        raise ValueError
    position = _integer(value["position"], 4, 4294967295)
    anchor = value.get("anchor", value)
    if set(anchor) != {"algorithm", "file", "position", "sha256"}:
        raise ValueError
    if anchor["algorithm"] != "MYSQL8_CRC32_PREFIX_V1" or anchor["file"] != name or anchor["position"] != position:
        raise ValueError
    _hash(anchor["sha256"])
    return name, position


def ler_manifesto(raw, hub, limit, *, verificar_estados=True):
    try:
        if not isinstance(raw, str) or not 1 <= len(raw.encode("utf-8")) <= limit:
            raise ValueError
        data = raw.encode("utf-8")
        doc = json_sem_duplicatas(raw.removeprefix("\ufeff"))
        if not isinstance(doc, dict) or doc["manifest_version"] != 1 or type(doc["manifest_version"]) is not int or doc["status"] != "VALIDO":
            raise ValueError
        kind = doc["type"]
        common = {"manifest_version", "type", "status", "started_at_utc", "completed_at_utc", "installation", "mysql", "archive"}
        extra = {"backup_id", "binlog"} if kind == "FULL" else {"capture_format", "coordinate_semantics", "incremental_id", "sequence", "full_backup_id", "full_manifest_sha256", "previous_id", "previous_manifest_sha256", "start", "end", "sources"}
        if kind not in ("FULL", "INCREMENTAL") or set(doc) != common | extra:
            raise ValueError
        identity = doc["installation"]
        if set(identity) != {"hub_uuid", "empresa_id", "loja_id", "retaguarda_hub_id"} or UUID(identity["hub_uuid"]) != hub.hub_uuid:
            raise ValueError
        # IDs do manifesto apenas corroboram o cadastro resolvido pelo token.
        if (_integer(identity["empresa_id"]), _integer(identity["loja_id"]), _integer(identity["retaguarda_hub_id"])) != (hub.loja.empresa_id, hub.loja_id, hub.pk):
            raise ValueError
        mysql = doc["mysql"]
        if set(mysql) != ({"version", "database", "server_uuid", "server_id", "binlog_format"} if kind == "FULL" else {"version", "database", "server_uuid"}):
            raise ValueError
        if mysql["database"] != "sysvarhub_db" or not re.fullmatch(r"8\.0\.[0-9]+", mysql["version"]):
            raise ValueError
        server = UUID(mysql["server_uuid"])
        if server.int == 0:
            raise ValueError
        started, generated = parse_datetime(doc["started_at_utc"]), parse_datetime(doc["completed_at_utc"])
        if not started or not generated or started.utcoffset() is None or generated.utcoffset() is None or generated < started:
            raise ValueError
        archive = doc["archive"]
        expected_name = "sysvarhub_db.sql.gz" if kind == "FULL" else "binlog-segment.sql.gz"
        expected_fields = {"name", "size_bytes", "sha256", "compression"} | ({"sql_size_bytes"} if kind == "FULL" else set())
        if set(archive) != expected_fields or archive["name"] != expected_name or archive["compression"] != "gzip":
            raise ValueError
        start = doc["binlog"] if kind == "FULL" else doc["start"]
        if set(start) != ({"file", "position", "anchor", "reference"} if kind == "FULL" else {"file", "position", "anchor"}):
            raise ValueError
        start_file, start_pos = _coordinate(start)
        result = dict(
            backup_uuid=UUID(doc["backup_id"] if kind == "FULL" else doc["incremental_id"]),
            instalacao_uuid=hub.hub_uuid, servidor_mysql_uuid=server, tipo=kind, gerado_em=generated,
            binlog_inicio_arquivo=start_file, binlog_inicio_posicao=start_pos,
            tamanho_bytes=_integer(archive["size_bytes"]), sha256=_hash(archive["sha256"]),
            manifesto_sha256=hashlib.sha256(data).hexdigest(), manifesto_versao=1, compressao="gzip",
            formato=HubBackup.FORMATO_FULL if kind == "FULL" else HubBackup.FORMATO_INCREMENTAL,
        )
        if kind == "FULL":
            if start["reference"] != "SNAPSHOT_START" or mysql["binlog_format"] != "ROW":
                raise ValueError
            _integer(mysql["server_id"], 1, 4294967295); _integer(archive["sql_size_bytes"])
        else:
            if doc["capture_format"] != HubBackup.FORMATO_INCREMENTAL or doc["coordinate_semantics"] != "START_INCLUSIVE_END_EXCLUSIVE":
                raise ValueError
            if set(doc["end"]) != {"file", "position", "anchor"}:
                raise ValueError
            end_file, end_pos = _coordinate(doc["end"])
            full_uuid, previous_uuid = UUID(doc["full_backup_id"]), UUID(doc["previous_id"])
            scope = HubBackup.objects.do_hub(hub).filter(instalacao_uuid=hub.hub_uuid)
            full = scope.filter(backup_uuid=full_uuid, tipo="FULL").first()
            previous = scope.filter(backup_uuid=previous_uuid).first()
            if not full or not previous or full.manifesto_sha256 != _hash(doc["full_manifest_sha256"]) or previous.manifesto_sha256 != _hash(doc["previous_manifest_sha256"]):
                raise ValueError
            if verificar_estados and any(item.estado_recebimento != "RECEBIDO" or item.estado_validacao in ("INVALIDO", "CADEIA_INTERROMPIDA") for item in (full, previous)):
                raise ValueError
            sources = doc["sources"]
            if not isinstance(sources, list) or not 1 <= len(sources) <= 4096:
                raise ValueError
            last_number = None
            for source in sources:
                file, pos = _coordinate(source)
                prefix, number = file.rsplit(".", 1)
                if last_number is not None and (prefix != last_prefix or int(number) != last_number + 1):
                    raise ValueError
                last_prefix, last_number = prefix, int(number)
            if sources[0]["file"] != start_file or sources[0]["position"] < start_pos or sources[-1]["file"] != end_file or sources[-1]["position"] != end_pos or sources[-1] != doc["end"]["anchor"]:
                raise ValueError
            result.update(full_referencia_id=full.pk, predecessor_id=previous.pk,
                          predecessor_sha256=previous.manifesto_sha256, sequencia=_integer(doc["sequence"]),
                          binlog_fim_arquivo=end_file, binlog_fim_posicao=end_pos)
        return result, doc, data
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValidationError("Manifesto ausente, divergente ou fora do contrato de backup do Hub.") from None

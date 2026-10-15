"""Container autenticado por registro; memoria limitada a uma parte.

AES-256-GCM/HKDF da biblioteca cryptography. O sal aleatorio identifica uma chave
por tentativa; cada registro usa nonce aleatorio de 96 bits. AAD liga registro,
ordem, identidade e manifesto ao header. Nao ha chave fixa ou plaintext em disco.
"""
import base64
import hashlib
import json
import os
import re
import struct

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MAGIC = b"SYSVAR-HUB-BACKUP\x01"
MAX_RECORD = 16 * 1024**2 + 28


def configuracao_criptografia():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        def unique_keys(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError
            return result
        keys = json.loads(settings.HUB_BACKUP_ENCRYPTION_KEYS, object_pairs_hook=unique_keys)
        if not isinstance(keys, dict) or not keys or len(keys) > 32:
            raise ValueError
        decoded = {}
        for key_id, value in keys.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key_id) or not isinstance(value, str):
                raise ValueError
            raw = base64.b64decode(value, validate=True)
            if len(raw) != 32:
                raise ValueError
            decoded[key_id] = raw
        if settings.HUB_BACKUP_ACTIVE_KEY_ID not in decoded:
            raise ValueError
        return decoded
    except (ValueError, TypeError, AttributeError, ImportError):
        raise ImproperlyConfigured("Criptografia de backups indisponivel; configurar chave externa valida.") from None


def header_upload(upload):
    b = upload.backup
    return json.dumps({
        "version": 1, "algorithm": "AES256_GCM_CHUNKS_V1", "key_id": upload.chave_id,
        "salt": upload.sal, "upload_uuid": str(upload.upload_uuid), "backup_uuid": str(b.backup_uuid),
        "manifest_sha256": b.manifesto_sha256, "size": b.tamanho_bytes, "sha256": b.sha256,
        "part_bytes": upload.tamanho_parte, "manifest_bytes": upload.manifesto_tamanho,
    }, sort_keys=True, separators=(",", ":")).encode("ascii")


def cipher_upload(upload, keys):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    try:
        salt = base64.b64decode(upload.sal, validate=True)
        if len(salt) != 32 or upload.chave_id not in keys or upload.criptografia != "AES256_GCM_CHUNKS_V1":
            raise ValueError
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                   info=b"SYSVAR-HUB-BACKUP-V1:" + upload.upload_uuid.bytes).derive(keys[upload.chave_id])
        return AESGCM(key)
    except (ValueError, KeyError):
        raise ImproperlyConfigured("Chave historica de backup indisponivel.") from None


def aad_registro(header, indice):
    # -1 e o manifesto. 0..N-1 sao as partes, com ordem autenticada.
    return MAGIC + hashlib.sha256(header).digest() + struct.pack(">q", indice)


def escrever_registro(stream, cipher, header, indice, data):
    if not 0 < len(data) <= MAX_RECORD - 28:
        raise ValueError("Tamanho de registro invalido.")
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, data, aad_registro(header, indice))
    stream.write(struct.pack(">I", len(nonce) + len(ciphertext)))
    stream.write(nonce)
    stream.write(ciphertext)


def ler_exato(stream, length):
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Container incompleto.")
    return data


def ler_registro(stream, cipher, header, indice, expected_size):
    length = struct.unpack(">I", ler_exato(stream, 4))[0]
    if length != expected_size + 28 or not 28 < length <= MAX_RECORD:
        raise ValueError("Registro criptografado invalido.")
    nonce = ler_exato(stream, 12)
    return cipher.decrypt(nonce, ler_exato(stream, length - 12), aad_registro(header, indice))


def escrever_header(stream, header):
    stream.write(MAGIC + struct.pack(">I", len(header)) + header)


def conferir_header(stream, header):
    if ler_exato(stream, len(MAGIC)) != MAGIC:
        raise ValueError("Container de backup invalido.")
    length = struct.unpack(">I", ler_exato(stream, 4))[0]
    if length != len(header) or length > 4096 or ler_exato(stream, length) != header:
        raise ValueError("Identidade do container divergente.")

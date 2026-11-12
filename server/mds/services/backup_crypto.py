from __future__ import annotations

import base64
import os
from pathlib import Path

MAGIC = b"MDSENC1"


def load_key(value: str | None = None) -> bytes | None:
    raw = value if value is not None else os.getenv("MDS_BACKUP_ENCRYPTION_KEY", "")
    if not raw.strip():
        return None
    key = base64.b64decode(raw.strip())
    if len(key) != 32:
        raise ValueError("MDS_BACKUP_ENCRYPTION_KEY: потрібно 32 байти в base64")
    return key


def encrypt_file(src: Path, key: bytes, dst: Path | None = None) -> Path:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    dst = dst or src.with_name(src.name + ".enc")
    nonce = os.urandom(12)
    dst.write_bytes(MAGIC + nonce + AESGCM(key).encrypt(nonce, src.read_bytes(), MAGIC))
    return dst


def decrypt_file(src: Path, key: bytes, dst: Path) -> Path:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data = src.read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError("Файл не є зашифрованою копією MDS")
    nonce, body = data[len(MAGIC):len(MAGIC) + 12], data[len(MAGIC) + 12:]
    dst.write_bytes(AESGCM(key).decrypt(nonce, body, MAGIC))
    return dst

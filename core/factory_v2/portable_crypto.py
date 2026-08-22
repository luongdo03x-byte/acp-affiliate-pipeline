"""Authenticated encryption for portable Account Factory bundles."""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_MAGIC = b"ACPPORT1"
_NONCE_SIZE = 12
_AAD = _MAGIC


def _decode_key(value: str) -> bytes:
    try:
        key = base64.b64decode(str(value or ""), validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise RuntimeError("PORTABLE_BUNDLE_KEY_INVALID") from exc
    if len(key) != 32:
        raise RuntimeError("PORTABLE_BUNDLE_KEY_INVALID")
    return key


def _atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(0o600)
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def encrypt_portable_bundle(source: Path, destination: Path, key: str) -> Path:
    """Encrypt one bundle with AES-256-GCM and write it atomically."""
    source = Path(source)
    destination = Path(destination)
    aes_key = _decode_key(key)
    try:
        plaintext = source.read_bytes()
    except OSError as exc:
        raise RuntimeError("PORTABLE_BUNDLE_ENCRYPT_FAILED") from exc

    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, _AAD)
    try:
        _atomic_write(destination, _MAGIC + nonce + ciphertext)
    except OSError as exc:
        raise RuntimeError("PORTABLE_BUNDLE_ENCRYPT_FAILED") from exc
    return destination


def decrypt_portable_bundle(source: Path, destination: Path, key: str) -> Path:
    """Decrypt one authenticated bundle without exposing partial plaintext."""
    source = Path(source)
    destination = Path(destination)
    aes_key = _decode_key(key)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise RuntimeError("PORTABLE_BUNDLE_DECRYPT_FAILED") from exc

    minimum_size = len(_MAGIC) + _NONCE_SIZE + 16
    if len(payload) < minimum_size or not payload.startswith(_MAGIC):
        raise RuntimeError("PORTABLE_BUNDLE_DECRYPT_FAILED")
    nonce_start = len(_MAGIC)
    nonce_end = nonce_start + _NONCE_SIZE
    nonce = payload[nonce_start:nonce_end]
    ciphertext = payload[nonce_end:]
    try:
        plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, _AAD)
    except (InvalidTag, ValueError) as exc:
        raise RuntimeError("PORTABLE_BUNDLE_DECRYPT_FAILED") from exc

    try:
        _atomic_write(destination, plaintext)
    except OSError as exc:
        raise RuntimeError("PORTABLE_BUNDLE_DECRYPT_FAILED") from exc
    return destination

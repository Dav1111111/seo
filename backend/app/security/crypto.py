from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


SECRET_PREFIX = "fernet:v1:"


class EncryptionKeyMissing(RuntimeError):
    """Raised when a secret must be encrypted but ENCRYPTION_KEY is empty."""


class SecretDecryptionError(RuntimeError):
    """Raised when an encrypted secret cannot be decrypted with current key."""


def _fernet(secret: str | None = None) -> Fernet:
    raw = (secret if secret is not None else settings.ENCRYPTION_KEY).strip()
    if not raw:
        raise EncryptionKeyMissing("ENCRYPTION_KEY is required")
    try:
        return Fernet(raw.encode("utf-8"))
    except ValueError:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
        return Fernet(key)


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value and value.startswith(SECRET_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if is_encrypted_secret(raw):
        return raw
    encrypted = _fernet().encrypt(raw.encode("utf-8")).decode("utf-8")
    return f"{SECRET_PREFIX}{encrypted}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if not is_encrypted_secret(raw):
        return raw
    payload = raw[len(SECRET_PREFIX):].encode("utf-8")
    try:
        return _fernet().decrypt(payload).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError("encrypted secret cannot be decrypted") from exc

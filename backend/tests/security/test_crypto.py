import pytest

from app.security import crypto


def test_encrypt_decrypt_secret_roundtrip(monkeypatch):
    monkeypatch.setattr(crypto.settings, "ENCRYPTION_KEY", "x" * 32)

    encrypted = crypto.encrypt_secret("oauth-token")

    assert encrypted is not None
    assert encrypted.startswith(crypto.SECRET_PREFIX)
    assert encrypted != "oauth-token"
    assert crypto.decrypt_secret(encrypted) == "oauth-token"


def test_encrypt_secret_is_idempotent_for_encrypted_value(monkeypatch):
    monkeypatch.setattr(crypto.settings, "ENCRYPTION_KEY", "x" * 32)

    encrypted = crypto.encrypt_secret("oauth-token")

    assert crypto.encrypt_secret(encrypted) == encrypted


def test_decrypt_secret_accepts_legacy_plaintext_value():
    assert crypto.decrypt_secret("legacy-token") == "legacy-token"


def test_encrypt_secret_requires_key(monkeypatch):
    monkeypatch.setattr(crypto.settings, "ENCRYPTION_KEY", "")

    with pytest.raises(crypto.EncryptionKeyMissing):
        crypto.encrypt_secret("oauth-token")

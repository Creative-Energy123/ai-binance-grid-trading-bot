from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE_BYTES = 12


class CredentialCipherError(RuntimeError):
    pass


def _key() -> bytes:
    raw = get_settings().credentials_encryption_key.strip()
    if not raw:
        raise CredentialCipherError(
            "CREDENTIALS_ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise CredentialCipherError("CREDENTIALS_ENCRYPTION_KEY must be urlsafe base64") from exc
    if len(key) != 32:
        raise CredentialCipherError(
            "CREDENTIALS_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256)"
        )
    return key


def encrypt_secret(plaintext: str) -> str:
    """AES-256-GCM encrypt a secret; returns base64(nonce || ciphertext)."""
    nonce = os.urandom(_NONCE_BYTES)
    blob = nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(blob).decode()


def decrypt_secret(token: str) -> str:
    blob = base64.b64decode(token)
    if len(blob) <= _NONCE_BYTES:
        raise CredentialCipherError("Ciphertext too short")
    return AESGCM(_key()).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None).decode()


def mask_key(value: str) -> str:
    """Safe-for-display fragment. Secrets are never returned in full by the API."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"

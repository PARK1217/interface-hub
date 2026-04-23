"""AES-GCM symmetric encryption for at-rest credentials (API keys, OAuth tokens)."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_NONCE_LEN = 12


def _key() -> bytes:
    raw = base64.b64decode(get_settings().secret_key)
    if len(raw) not in (16, 24, 32):
        raise ValueError("SECRET_KEY must decode to 16/24/32 bytes for AES")
    return raw


def encrypt_secret(plaintext: str) -> str:
    """Encrypt and return `nonce||ciphertext` base64-encoded."""
    if not plaintext:
        return ""
    aes = AESGCM(_key())
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    blob = base64.b64decode(token)
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    aes = AESGCM(_key())
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
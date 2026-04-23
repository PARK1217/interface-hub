"""인터페이스 인증 시크릿 (API key, OAuth 토큰 등) 의 at-rest 암호화.

AES-GCM 256-bit 사용. SECRET_KEY 는 base64 디코딩 후 정확히 16/24/32 바이트
여야 함 — 이전에 잘못된 길이 (33바이트) 로 시작해서 모든 시크릿 저장이
ValueError 로 깨졌던 사례 있음. 운영 배포 시 반드시
``python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"``
로 새로 생성한 32바이트 키로 교체할 것.
"""

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
"""Phase 12D: symmetric encryption for tenant-owned integration credentials
at rest (OAuth tokens, API keys stored per-tenant rather than as a single
platform-level env var). Uses `cryptography`'s Fernet (AES-128-CBC +
HMAC-SHA256, authenticated) — already a transitive dependency via
`python-jose[cryptography]`, no new package added.

This is deliberately a thin, swappable boundary: `encrypt_credential`/
`decrypt_credential` are the only two functions anything else in the
codebase should call. A real production deployment should point
INTEGRATION_CREDENTIAL_ENCRYPTION_KEY at a key from a real secret manager
(AWS KMS/Secrets Manager, GCP Secret Manager, Vault) rather than a raw env
var — swapping that in later only requires changing get_settings() and
this module, not any caller.
"""

from __future__ import annotations

import base64
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_INSECURE_DEFAULT_KEY = "insecure-dev-only-integration-credential-key-000"


class CredentialDecryptionError(Exception):
    pass


def _get_fernet() -> Fernet:
    settings = get_settings()
    raw_key = settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY or _INSECURE_DEFAULT_KEY
    # Fernet requires a 32-byte urlsafe-base64 key; derive one deterministically
    # from whatever string is configured so operators can set a plain
    # passphrase-shaped env var rather than a pre-formatted Fernet key.
    import hashlib

    digest = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def is_using_insecure_default_key() -> bool:
    settings = get_settings()
    return not settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY


def encrypt_credential(data: dict) -> str:
    """`data` is a plain dict of credential fields (api_key, access_token,
    refresh_token, client_id, ...) — shape varies per provider, this layer
    doesn't care. Returns an opaque, authenticated ciphertext string safe
    to store in a DB column."""
    plaintext = json.dumps(data).encode()
    return _get_fernet().encrypt(plaintext).decode()


def decrypt_credential(blob: str) -> dict:
    try:
        plaintext = _get_fernet().decrypt(blob.encode())
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Could not decrypt stored credential — wrong key, or the key changed since encryption"
        ) from exc
    return json.loads(plaintext)

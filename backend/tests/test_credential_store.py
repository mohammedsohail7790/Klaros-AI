"""Phase 12D: credential encryption at rest — pure crypto, fully testable
without any real provider credential."""

import pytest

from app.core.config import get_settings
from app.integrations.credential_store import (
    CredentialDecryptionError,
    decrypt_credential,
    encrypt_credential,
    is_using_insecure_default_key,
)


def test_round_trip_preserves_data() -> None:
    data = {"api_key": "sk_test_fake123", "extra": "value"}
    blob = encrypt_credential(data)
    assert blob != str(data)  # never stored as plaintext
    assert "sk_test_fake123" not in blob
    assert decrypt_credential(blob) == data


def test_ciphertext_is_opaque_and_unique_per_call() -> None:
    data = {"api_key": "same-value"}
    blob1 = encrypt_credential(data)
    blob2 = encrypt_credential(data)
    # Fernet includes a random IV/nonce — same plaintext must not produce
    # identical ciphertext (defends against pattern analysis).
    assert blob1 != blob2
    assert decrypt_credential(blob1) == decrypt_credential(blob2) == data


def test_decrypting_with_wrong_key_fails_loudly(monkeypatch) -> None:
    data = {"api_key": "sk_test_fake123"}
    blob = encrypt_credential(data)

    settings = get_settings()
    monkeypatch.setattr(settings, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY", "a-totally-different-key")
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(blob)


def test_tampered_ciphertext_is_rejected() -> None:
    data = {"api_key": "sk_test_fake123"}
    blob = encrypt_credential(data)
    tampered = blob[:-4] + ("A" if blob[-4] != "A" else "B") + blob[-3:]
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(tampered)


def test_insecure_default_key_is_flagged_when_unset(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY", None)
    assert is_using_insecure_default_key() is True


def test_configured_key_is_not_flagged_as_insecure(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY", "a-real-random-key-value")
    assert is_using_insecure_default_key() is False

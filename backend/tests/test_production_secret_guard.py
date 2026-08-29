"""Production audit: the API must refuse to boot in production with the
publicly-visible default JWT_SECRET — anyone who has read this open-source
codebase can forge a valid token for any tenant/user/role against a
deployment that forgot to override it."""

import pytest

from app.main import _assert_production_secrets_are_real, _INSECURE_DEFAULT_JWT_SECRET, settings

pytestmark = pytest.mark.asyncio


async def test_refuses_to_start_in_production_with_default_secret() -> None:
    original_env, original_secret = settings.ENV, settings.JWT_SECRET
    try:
        settings.ENV = "production"
        settings.JWT_SECRET = _INSECURE_DEFAULT_JWT_SECRET
        with pytest.raises(RuntimeError, match="insecure default"):
            _assert_production_secrets_are_real()
    finally:
        settings.ENV, settings.JWT_SECRET = original_env, original_secret


async def test_starts_in_production_with_a_real_secret() -> None:
    original_env, original_secret = settings.ENV, settings.JWT_SECRET
    original_cred_key = settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY
    try:
        settings.ENV = "production"
        settings.JWT_SECRET = "a-real-random-secret-set-by-the-operator"
        settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY = "a-real-random-credential-key-set-by-the-operator"
        _assert_production_secrets_are_real()  # must not raise
    finally:
        settings.ENV, settings.JWT_SECRET = original_env, original_secret
        settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY = original_cred_key


async def test_refuses_to_start_in_production_with_default_credential_encryption_key() -> None:
    original_env, original_secret = settings.ENV, settings.JWT_SECRET
    original_cred_key = settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY
    try:
        settings.ENV = "production"
        settings.JWT_SECRET = "a-real-random-secret-set-by-the-operator"
        settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY = None
        with pytest.raises(RuntimeError, match="INTEGRATION_CREDENTIAL_ENCRYPTION_KEY"):
            _assert_production_secrets_are_real()
    finally:
        settings.ENV, settings.JWT_SECRET = original_env, original_secret
        settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY = original_cred_key


async def test_development_env_is_unaffected_by_the_default_secret() -> None:
    original_env, original_secret = settings.ENV, settings.JWT_SECRET
    try:
        settings.ENV = "development"
        settings.JWT_SECRET = _INSECURE_DEFAULT_JWT_SECRET
        _assert_production_secrets_are_real()  # must not raise — dev is allowed the default
    finally:
        settings.ENV, settings.JWT_SECRET = original_env, original_secret

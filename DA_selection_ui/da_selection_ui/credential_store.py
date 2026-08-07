"""Secure local credential storage for DiscoveryAgent.

Secrets are stored exclusively through the operating system keyring. There is
deliberately no file fallback.
"""
from __future__ import annotations

SERVICE_NAME = "DiscoveryAgent"
ANTHROPIC_USERNAME = "anthropic_api_key"


class CredentialStoreError(RuntimeError):
    """Safe, secret-free error raised when secure storage is unavailable."""


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise CredentialStoreError(
            "Secure credential storage is unavailable. Install the keyring dependency and configure an operating system credential manager."
        ) from exc

    backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) <= 0:
        raise CredentialStoreError(
            "No secure operating system credential manager is available. Configure Keychain, Credential Manager, or Secret Service and try again."
        )
    return keyring, KeyringError


def set_anthropic_api_key(secret: str) -> None:
    keyring, keyring_error = _keyring()
    try:
        keyring.set_password(SERVICE_NAME, ANTHROPIC_USERNAME, secret)
    except keyring_error as exc:
        raise CredentialStoreError(
            "DiscoveryAgent could not save the API key in the operating system credential manager."
        ) from exc


def get_anthropic_api_key() -> str | None:
    keyring, keyring_error = _keyring()
    try:
        return keyring.get_password(SERVICE_NAME, ANTHROPIC_USERNAME)
    except keyring_error as exc:
        raise CredentialStoreError(
            "DiscoveryAgent could not access the operating system credential manager."
        ) from exc


def has_anthropic_api_key() -> bool:
    return get_anthropic_api_key() is not None


def delete_anthropic_api_key() -> None:
    keyring, keyring_error = _keyring()
    try:
        keyring.delete_password(SERVICE_NAME, ANTHROPIC_USERNAME)
    except keyring_error as exc:
        # Missing credentials are an idempotent success. Other keyring failures
        # remain fail-closed.
        try:
            from keyring.errors import PasswordDeleteError
        except ImportError:  # pragma: no cover - keyring was already imported
            PasswordDeleteError = ()  # type: ignore
        if isinstance(exc, PasswordDeleteError):
            return
        raise CredentialStoreError(
            "DiscoveryAgent could not remove the API key from the operating system credential manager."
        ) from exc

"""Secret storage backed by the OS credential store.

Uses `keyring` (macOS Keychain, Windows Credential Manager, libsecret/kwallet
on Linux) when installed. Without it, every lookup fails cleanly and profiles
that reference `{{ secret.NAME }}` report a readable error instead of leaking
an empty string into a command line.

Secrets are never written to the log or to run history.
"""
from __future__ import annotations

SERVICE = "profile-auto-launcher"


class SecretError(RuntimeError):
    """Raised when a secret cannot be read or the backend is unavailable."""


def _backend():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SecretError(
            "keyring is not installed — run `pip install .[secrets]`"
        ) from exc
    return keyring


def available() -> bool:
    try:
        _backend()
        return True
    except SecretError:
        return False


def get(name: str) -> str:
    keyring = _backend()
    try:
        value = keyring.get_password(SERVICE, name)
    except Exception as exc:  # keyring raises backend-specific errors
        raise SecretError(f"keyring backend failed: {exc}") from exc
    if value is None:
        raise SecretError(f"secret '{name}' is not set (palaunch secret set {name})")
    return value


def set_secret(name: str, value: str) -> None:
    keyring = _backend()
    try:
        keyring.set_password(SERVICE, name, value)
    except Exception as exc:
        raise SecretError(f"keyring backend failed: {exc}") from exc
    _remember(name)


def delete(name: str) -> None:
    keyring = _backend()
    try:
        keyring.delete_password(SERVICE, name)
    except Exception as exc:
        raise SecretError(f"keyring backend failed: {exc}") from exc
    _forget(name)


# ── name index ───────────────────────────────────────────────────────────
# Keyring backends cannot enumerate the entries of a service, so `palaunch
# secret list` reads a plain index of *names* we have written. Values stay
# in the credential store; the index holds no secret material.


def _index_path():
    from launcher.config import config_dir

    return config_dir() / "secret-names.txt"


def _load_index() -> list[str]:
    path = _index_path()
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _write_index(names: list[str]) -> None:
    path = _index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(set(names))) + "\n", encoding="utf-8")
    except OSError:
        pass


def _remember(name: str) -> None:
    names = _load_index()
    if name not in names:
        _write_index([*names, name])


def _forget(name: str) -> None:
    _write_index([n for n in _load_index() if n != name])


def list_names() -> list[str]:
    """Names we know about. A secret written by other means won't appear."""
    return _load_index()

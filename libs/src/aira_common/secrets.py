"""Where secrets come from: HashiCorp Vault, read once at start-up (`FRD-116`).

One loader for both planes, so "where do secrets come from" cannot diverge between them.

- **Fail closed.** A configured Vault that cannot be reached or read is a start-up failure, never a
  fallback to the environment — that would be a silent downgrade to stale or development values
  (`ADR-0007`).
- **Never a request-path dependency.** Secrets are read once; Vault going down later does not
  affect a running service, and rotation is a restart (§5.4).
- **Values never surface** — not in logs, spans, errors, `/readyz` or a traceback. Only the *names*
  resolved, and where each came from, are reported.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from aira_common.integration_debug import (
    UnknownIntegration,
    configure_integration_debug,
    watch,
)
from aira_common.logging import configure_logging, get_logger

#: Where an AppRole secret-id may come from, in lookup order: an environment variable (CI, simple
#: deployments), a mounted file (a Kubernetes projected volume keeps it out of the manifest), and a
#: token for local development. The secret-id is itself a secret, so it must arrive by a path Vault
#: did not provide.
SECRET_ID_ENV = "VAULT_SECRET_ID"
SECRET_ID_FILE_ENV = "VAULT_SECRET_ID_FILE"
DEV_TOKEN_ENV = "VAULT_TOKEN"

DEFAULT_TIMEOUT = 10.0

_log = get_logger("aira_common.secrets")


class VaultUnavailable(Exception):
    """Vault is configured and could not be used. **A startup failure, never a fallback.**"""


class SecretMissing(Exception):
    """A key the deployment requires is not at the configured path.

    Distinct from :class:`VaultUnavailable`: "Vault is down" and "nobody has written that key yet"
    call for different actions by different people.
    """


@dataclass(frozen=True, slots=True)
class VaultConfig:
    """Everything needed to read one path. Empty ``address`` means "no Vault" — see FR-7."""

    address: str = ""
    mount: str = "secret"
    path: str = "aira"
    role_id: str = ""
    namespace: str = ""
    timeout: float = DEFAULT_TIMEOUT

    @property
    def configured(self) -> bool:
        return bool(self.address and (self.role_id or _dev_token()))

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> VaultConfig:
        """Read the Vault settings themselves from the environment.

        They identify rather than authorise, so they are ordinary configuration. The one secret,
        the secret-id, is read by :func:`_secret_id` and never stored here, so it cannot reach a
        repr, a log line or a pickled settings object.
        """
        source = env if env is not None else dict(os.environ)
        return cls(
            address=source.get("VAULT_ADDR", "").strip(),
            mount=source.get("VAULT_MOUNT", "secret").strip() or "secret",
            path=source.get("VAULT_PATH", "aira").strip() or "aira",
            role_id=source.get("VAULT_ROLE_ID", "").strip(),
            namespace=source.get("VAULT_NAMESPACE", "").strip(),
            # Empty means unset: compose's `${VAULT_TIMEOUT:-}` yields "" and `float("")` raises.
            timeout=float(source.get("VAULT_TIMEOUT", "").strip() or DEFAULT_TIMEOUT),
        )


class VaultSourceCache:
    """The names loaded at startup, so `/readyz` never re-reads Vault to answer a health check."""

    _keys: tuple[str, ...] = ()

    @classmethod
    def remember(cls, secrets: dict[str, str]) -> None:
        cls._keys = tuple(sorted(secrets))

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        return cls._keys


def _dev_token() -> str:
    return os.environ.get(DEV_TOKEN_ENV, "").strip()


def _secret_id() -> tuple[str, str]:
    """The AppRole secret-id and **the name of where it came from**, which is logged instead."""
    direct = os.environ.get(SECRET_ID_ENV, "").strip()
    if direct:
        return direct, SECRET_ID_ENV

    path = os.environ.get(SECRET_ID_FILE_ENV, "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip(), SECRET_ID_FILE_ENV
        except OSError as exc:
            # Named, not swallowed: falling through to the next source would start the service
            # with the wrong credential rather than with none.
            raise VaultUnavailable(
                f"{SECRET_ID_FILE_ENV} points at '{path}', which cannot be read "
                f"({type(exc).__name__})."
            ) from exc
    return "", ""


class VaultClient:
    """A minimal Vault KV-v2 reader. Injectable client so the whole path is testable offline."""

    def __init__(self, config: VaultConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(timeout=config.timeout, verify=True)
        #: Whether this object made the client and owes it a `close()`. An injected one belongs to
        #: the caller.
        self._owns_client = client is None

    def close(self) -> None:
        """Release the connection pool, if this object opened one. Idempotent.

        Secrets are read once, so an open pool would outlive the only work it was created for.
        """
        if self._owns_client:
            self._client.close()

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"X-Vault-Token": token}
        if self._config.namespace:
            headers["X-Vault-Namespace"] = self._config.namespace
        return headers

    def login(self) -> str:
        """Exchange the AppRole credentials for a client token, or return a dev token as-is.

        A dev token is accepted only because `make up` runs Vault in dev mode (the escape hatch
        `ADR-0007` allows for `local`); it is logged, so a deployment running on one is visible.
        """
        token = _dev_token()
        if token and not self._config.role_id:
            _log.warning("vault_dev_token_used", address=self._config.address)
            return token

        secret_id, source = _secret_id()
        if not secret_id:
            raise VaultUnavailable(
                "VAULT_ROLE_ID is set but no secret-id was found. Looked for "
                f"{SECRET_ID_ENV}, then {SECRET_ID_FILE_ENV}. Without one there is no way to "
                "authenticate, and starting anyway would run on whatever the environment holds."
            )

        try:
            with watch(
                "vault", "login", target=self._config.address, secret_id_source=source
            ) as call:
                response = self._client.post(
                    f"{self._config.address}/v1/auth/approle/login",
                    json={"role_id": self._config.role_id, "secret_id": secret_id},
                    headers={"X-Vault-Namespace": self._config.namespace}
                    if self._config.namespace
                    else {},
                )
                # The status, never the body: a Vault error echoes the request, which carries the
                # secret-id.
                call.note(status=response.status_code)
                if response.status_code != httpx.codes.OK:
                    call.failed(f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            raise VaultUnavailable(
                f"Vault at {self._config.address} is unreachable ({type(exc).__name__})."
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise VaultUnavailable(
                f"Vault refused the AppRole login with {response.status_code}. The secret-id came "
                f"from {source}; check that it is current and that the role may read "
                f"{self._config.mount}/{self._config.path}."
            )

        auth = response.json().get("auth") or {}
        client_token = str(auth.get("client_token") or "")
        if not client_token:
            raise VaultUnavailable("Vault's login response carried no client token.")
        _log.info("vault_authenticated", address=self._config.address, secret_id_source=source)
        return client_token

    def read(self, token: str) -> dict[str, str]:
        """Read the KV-v2 path and return its data, as strings.

        Coerced to `str` because every consumer is a settings field parsed from text; a JSON number
        would otherwise behave differently from the same value typed as a string.
        """
        url = f"{self._config.address}/v1/{self._config.mount}/data/{self._config.path}"
        try:
            with watch(
                "vault",
                "read",
                target=url,
                mount=self._config.mount,
                path=self._config.path,
            ) as call:
                response = self._client.get(url, headers=self._headers(token))
                call.note(status=response.status_code)
                if response.status_code != httpx.codes.OK:
                    call.failed(f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            raise VaultUnavailable(
                f"Vault at {self._config.address} is unreachable ({type(exc).__name__})."
            ) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise SecretMissing(
                f"No secret at {self._config.mount}/{self._config.path}. Vault answered, so this "
                "is a path or a permission, not an outage."
            )
        if response.status_code != httpx.codes.OK:
            raise VaultUnavailable(
                f"Vault answered {response.status_code} reading "
                f"{self._config.mount}/{self._config.path}."
            )

        data = (response.json().get("data") or {}).get("data") or {}
        return {str(key): str(value) for key, value in data.items() if value is not None}


def _say_something_before_the_settings_exist() -> None:
    """Configure logging and the `FRD-617` channel, because nothing else can have yet.

    Vault is read while the settings object is being built (`VaultSource` is a settings source),
    before any entry point has called `configure_logging` or `configure_integration_debug`. The
    channel's setting is therefore read from the environment, and a value that does not parse is
    left off here: the settings validator refuses the process a moment later, with a better
    message. Runs only when Vault is configured.
    """
    # Only if nobody has: never reconfigure a structlog that is already running (including
    # `structlog.testing.capture_logs` in the suite).
    if not structlog.is_configured():
        configure_logging()
    with contextlib.suppress(UnknownIntegration):
        configure_integration_debug(os.environ.get("AIRA_DEBUG_INTEGRATIONS", ""))


def load_secrets(
    config: VaultConfig | None = None, client: httpx.Client | None = None
) -> dict[str, str]:
    """Every secret at the configured path, or an empty mapping when Vault is not configured.

    The empty mapping means only "no Vault configured" (FR-7). Every other outcome raises.
    """
    config = config or VaultConfig.from_env()
    if not config.configured:
        return {}

    _say_something_before_the_settings_exist()
    vault = VaultClient(config, client)
    try:
        secrets = vault.read(vault.login())
    finally:
        # In a `finally`, so a failed login or read releases the pool as well.
        vault.close()
    # Names only: a log line is the likeliest place for a secret to escape.
    VaultSourceCache.remember(secrets)
    _log.info(
        "vault_secrets_loaded",
        address=config.address,
        path=f"{config.mount}/{config.path}",
        keys=sorted(secrets),
        count=len(secrets),
    )
    return secrets


def resolve(
    secrets: dict[str, str], env_prefix: str = "AIRA_", env: dict[str, str] | None = None
) -> dict[str, str]:
    """Merge Vault over the environment, in the prefix the settings classes expect (FR-3).

    Vault wins where a key is in both; there is no third source, so a key in neither is absent and
    whatever requires it says so at start-up. Keys match case-insensitively and with or without the
    prefix, because a key that is present but spelled differently looks exactly like a missing one.
    """
    source = dict(env if env is not None else os.environ)
    merged = dict(source)
    for key, value in secrets.items():
        name = key.strip().upper()
        merged[name if name.startswith(env_prefix) else f"{env_prefix}{name}"] = value
    return merged


def secrets_state(config: VaultConfig | None = None) -> dict[str, Any]:
    """Where this process's secrets came from — **names only, never values**.

    `source` answers "is Vault actually being used?", which a configured and an unconfigured
    secret store otherwise do not distinguish from outside.
    """
    config = config or VaultConfig.from_env()
    if not config.configured:
        return {"source": "environment", "vault_configured": False}
    return {
        "source": "vault",
        "vault_configured": True,
        "address": config.address,
        "path": f"{config.mount}/{config.path}",
        # Which keys Vault supplied: "did it pick up the new one?" without "what is it?".
        "keys": sorted(VaultSourceCache.keys()),
        # A root token standing in for an AppRole is a local convenience and a production accident.
        "dev_token": bool(_dev_token()),
    }

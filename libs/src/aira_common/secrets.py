"""Where secrets come from (FRD-116).

`CLAUDE.md` §2 has said "secrets only in HashiCorp Vault" since Phase 0, and Vault has been in the
Compose stack for as long — with **no code reading from it**. Every credential this system holds
has been an environment variable, which is the state the policy exists to prevent.

One loader, used by both planes, for the reason `aira_common.roles` is shared: two implementations
of "where do secrets come from" diverge, and the divergence is discovered in whichever plane was
not under test.

Three rules shape everything here.

**Fail closed.** The tempting behaviour when Vault is unreachable is to fall back to the
environment and carry on. That turns a broken secret store into a *silent downgrade* — and in that
scenario the environment usually holds a stale or development value, so the service starts, looks
healthy, and is wrong. `ADR-0007` already established the principle for `SECRET_KEY`; this extends
it.

**Never a request-path dependency.** Secrets are read once, at startup. Vault going down an hour
later must not affect a running gateway, which is also why there is no live re-read (§5.4: rotation
is a restart, recorded as a decision so its absence is not later read as an oversight).

**Values never surface.** Not in logs, spans, errors, `/readyz`, or a traceback. What is logged is
the *names* resolved and where each came from — enough to answer "did it pick up the new one?"
without ever answering "what is it?".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from aira_common.logging import get_logger

_log = get_logger("aira_common.secrets")

#: Where a secret-id may come from, in the order it is looked for. The awkward part of AppRole is
#: that the secret-id is *itself* a secret, so it has to arrive by a path Vault did not provide.
#: The order reflects how deployments actually work: an environment variable (CI, simple
#: deployments), a mounted file (a Kubernetes projected volume puts it on a tmpfs rather than in a
#: manifest), and a token for local development.
SECRET_ID_ENV = "VAULT_SECRET_ID"
SECRET_ID_FILE_ENV = "VAULT_SECRET_ID_FILE"
DEV_TOKEN_ENV = "VAULT_TOKEN"

DEFAULT_TIMEOUT = 10.0


class VaultUnavailable(Exception):
    """Vault is configured and could not be used. **A startup failure, never a fallback.**"""


class SecretMissing(Exception):
    """A key the deployment requires is not at the configured path.

    Distinct from :class:`VaultUnavailable` on purpose: "Vault is down" and "nobody has written
    that key yet" call for different actions by different people, and one message covering both
    sends whoever reads it to the wrong one.
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

        These are *not* secrets — an address and a role id identify, they do not authorise — so
        they come from the environment like any other configuration. The one value that is a
        secret, the secret-id, is handled by :func:`_secret_id` and never stored on this object,
        so it cannot reach a repr, a log line, or a pickled settings object.
        """
        source = env if env is not None else dict(os.environ)
        return cls(
            address=source.get("VAULT_ADDR", "").strip(),
            mount=source.get("VAULT_MOUNT", "secret").strip() or "secret",
            path=source.get("VAULT_PATH", "aira").strip() or "aira",
            role_id=source.get("VAULT_ROLE_ID", "").strip(),
            namespace=source.get("VAULT_NAMESPACE", "").strip(),
            # An **empty** value means unset, not "parse this". A compose file writing
            # `${VAULT_TIMEOUT:-}` produces an empty string, and `float("")` raises — which is how
            # a boot failed the day these variables were first passed through. The same trap
            # `BaseAiraSettings._empty_means_unset` was written for, one module over.
            timeout=float(source.get("VAULT_TIMEOUT", "").strip() or DEFAULT_TIMEOUT),
        )


def _dev_token() -> str:
    return os.environ.get(DEV_TOKEN_ENV, "").strip()


def _secret_id() -> tuple[str, str]:
    """The AppRole secret-id and **the name of where it came from**.

    The name is returned so the caller can log which path was taken. "Which of the three did it
    use" is exactly the question asked when a deployment picks up an unexpected credential, and it
    is answerable without ever logging the value.
    """
    direct = os.environ.get(SECRET_ID_ENV, "").strip()
    if direct:
        return direct, SECRET_ID_ENV

    path = os.environ.get(SECRET_ID_FILE_ENV, "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip(), SECRET_ID_FILE_ENV
        except OSError as exc:
            # Named, not swallowed: a projected volume that failed to mount is a deployment
            # problem, and falling through to the next source would start the service with the
            # wrong credential rather than with none.
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
        #: Whether this object made the client and therefore owes it a `close()`. An injected one
        #: belongs to the caller — the tests pass a `MockTransport` client and reuse it — and
        #: closing somebody else's is a harder bug to find than leaking one's own.
        self._owns_client = client is None

    def close(self) -> None:
        """Release the connection pool, if this object opened one.

        Secrets are read **once, at startup** (§"never a request-path dependency"), so the socket
        this holds is used for two requests and then never again. Leaving it open is a small leak
        and a lasting one: the pool outlives the only work it was created for, and in a process
        that reads a second path it becomes one pool per read. Idempotent, because a `finally` that
        can run twice is easier to get right than one that must not.
        """
        if self._owns_client:
            self._client.close()

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"X-Vault-Token": token}
        if self._config.namespace:
            headers["X-Vault-Namespace"] = self._config.namespace
        return headers

    def login(self) -> str:
        """Exchange the AppRole credentials for a client token, or a dev token as-is.

        A development token is accepted **only** because `make up` runs Vault in dev mode with a
        root token; it is the same escape hatch `ADR-0007` allows for `local` and it is logged, so
        a deployment that reached production on one is visible rather than merely possible.
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
            response = self._client.post(
                f"{self._config.address}/v1/auth/approle/login",
                json={"role_id": self._config.role_id, "secret_id": secret_id},
                headers={"X-Vault-Namespace": self._config.namespace}
                if self._config.namespace
                else {},
            )
        except httpx.HTTPError as exc:
            raise VaultUnavailable(
                f"Vault at {self._config.address} is unreachable ({type(exc).__name__})."
            ) from exc

        if response.status_code != httpx.codes.OK:
            # The body may echo the request. Only the status is reported.
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

        Values are coerced to `str` because every consumer is a settings field parsed from text —
        letting a JSON number through would make the same key behave differently depending on how
        somebody happened to type it into Vault.
        """
        url = f"{self._config.address}/v1/{self._config.mount}/data/{self._config.path}"
        try:
            response = self._client.get(url, headers=self._headers(token))
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


def load_secrets(
    config: VaultConfig | None = None, client: httpx.Client | None = None
) -> dict[str, str]:
    """Every secret at the configured path, or an empty mapping when Vault is not configured.

    **The empty mapping is only ever returned for "no Vault configured"** (FR-7 — a laptop and the
    hermetic suite behave exactly as before). Every other outcome raises: a configured Vault that
    cannot be reached or read is a boot failure, because the alternative is a service that runs on
    the environment's stale values and looks healthy doing it.
    """
    config = config or VaultConfig.from_env()
    if not config.configured:
        return {}

    vault = VaultClient(config, client)
    try:
        secrets = vault.read(vault.login())
    finally:
        # In a `finally`, so a failed login or read releases the pool as well. Every path out of
        # here except "no Vault configured" is a startup failure, and a process on its way down
        # holding an open connection to a secret store is the one moment it should not.
        vault.close()
    # Names only. Answering "did it pick up the new key?" must never require answering "what is
    # it?" — and a log line is the single most likely place for a secret to escape.
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

    Vault wins where a key exists in both, and the environment fills the rest. There is **no
    silent third source**: a key in neither is absent, and whatever requires it says so at startup
    rather than proceeding with an empty string — which is the failure this whole module exists to
    prevent, since an empty credential authenticates as nobody and reads as a permissions problem.

    Keys are matched case-insensitively and with or without the prefix, because a human writing
    them into Vault will use whichever form they read in the documentation, and a secret that is
    present but spelled differently is indistinguishable from one that is missing.
    """
    source = dict(env if env is not None else os.environ)
    merged = dict(source)
    for key, value in secrets.items():
        name = key.strip().upper()
        merged[name if name.startswith(env_prefix) else f"{env_prefix}{name}"] = value
    return merged


def secrets_state(config: VaultConfig | None = None) -> dict[str, Any]:
    """Where this process's secrets came from — **names only, never values**.

    Exists because its absence cost three days. `FRD-116` shipped Vault reading, the compose stack
    never passed `VAULT_ADDR`, and every credential quietly came from the environment while the
    feature was recorded as done. Nothing reported the difference, so there was nothing to notice:
    a configured secret store and an unconfigured one looked identical from outside.

    `source` is the answer to "is Vault actually being used?", which should never have required
    reading a compose file to find out.
    """
    config = config or VaultConfig.from_env()
    if not config.configured:
        return {"source": "environment", "vault_configured": False}
    return {
        "source": "vault",
        "vault_configured": True,
        "address": config.address,
        "path": f"{config.mount}/{config.path}",
        # Which keys Vault supplied, so "did it pick up the new one?" is answerable without ever
        # answering "what is it?".
        "keys": sorted(VaultSourceCache.keys()),
        # A root token standing in for an AppRole is a local convenience and a production
        # accident; saying so here means it cannot pass unnoticed.
        "dev_token": bool(_dev_token()),
    }


class VaultSourceCache:
    """The names loaded at startup, so `/readyz` never re-reads Vault to answer a health check."""

    _keys: tuple[str, ...] = ()

    @classmethod
    def remember(cls, secrets: dict[str, str]) -> None:
        cls._keys = tuple(sorted(secrets))

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        return cls._keys

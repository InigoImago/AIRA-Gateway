import aira_management.apps.health.views as health_views
from django.test import Client

from aira_common.health import CheckResult


def test_healthz() -> None:
    resp = Client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ready(monkeypatch) -> None:
    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_views, "check_tcp", all_ok)

    resp = Client().get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"]["ok"] is True
    assert body["checks"]["kafka"]["ok"] is True


def test_readyz_not_ready(monkeypatch) -> None:
    async def all_fail(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=False, detail=f"{host}:{port} unreachable")

    monkeypatch.setattr(health_views, "check_tcp", all_fail)

    resp = Client().get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "unreachable" in body["checks"]["kafka"]["detail"]


def test_readyz_without_a_broker_asks_only_the_database(monkeypatch) -> None:
    """An installation without Kafka has no broker to reach, and a probe of a default address held
    it `not_ready` for good. It is said instead, as the gateway says it."""
    from aira_management.config.runtime import get_settings

    asked: list[str] = []

    async def record(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        asked.append(name)
        return CheckResult(name=name, ok=True)

    without = get_settings().model_copy(update={"kafka_bootstrap_servers": ""})
    monkeypatch.setattr(health_views, "get_settings", lambda: without)
    monkeypatch.setattr(health_views, "check_tcp", record)

    resp = Client().get("/readyz")

    assert resp.status_code == 200
    assert asked == ["postgres"]
    assert resp.json()["checks"]["kafka"] == {
        "ok": True,
        "detail": "not configured",
        "required": False,
    }


def test_readyz_dials_an_ipv6_broker_by_its_address(monkeypatch) -> None:
    from aira_management.config.runtime import get_settings

    dialled: list[tuple[str, int]] = []

    async def record(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        dialled.append((host, port))
        return CheckResult(name=name, ok=True)

    ipv6 = get_settings().model_copy(update={"kafka_bootstrap_servers": "[::1]:9093"})
    monkeypatch.setattr(health_views, "get_settings", lambda: ipv6)
    monkeypatch.setattr(health_views, "check_tcp", record)

    assert Client().get("/readyz").status_code == 200
    assert ("::1", 9093) in dialled

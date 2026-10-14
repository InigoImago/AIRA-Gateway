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


def test_a_kafka_outage_degrades_the_service_and_keeps_it_in_rotation(monkeypatch) -> None:
    """Every change is written to the outbox in Postgres and published later; while the bus is
    down the console keeps working. Reporting `not_ready` took the service out of rotation over a
    dependency its requests do not use."""

    async def only_the_database(
        name: str, host: str, port: int, *, timeout: float = 1.0
    ) -> CheckResult:
        return CheckResult(name=name, ok=name == "postgres", detail=None)

    monkeypatch.setattr(health_views, "check_tcp", only_the_database)

    resp = Client().get("/readyz")

    assert resp.status_code == 200, "a Kafka outage took the service out of rotation"
    body = resp.json()
    assert body["status"] == "ready"
    assert body["degraded"] is True
    assert body["checks"]["kafka"] == {"ok": False, "detail": "unreachable", "required": False}


def test_the_cluster_is_reachable_while_any_broker_is(monkeypatch) -> None:
    """A Kafka client is handed the whole list and needs one broker. The first broker alone was
    asked, and its outage read as the cluster's."""
    from aira_management.config.runtime import get_settings

    async def first_down(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=host != "first")

    two = get_settings().model_copy(update={"kafka_bootstrap_servers": "first:9092,second:9092"})
    monkeypatch.setattr(health_views, "get_settings", lambda: two)
    monkeypatch.setattr(health_views, "check_tcp", first_down)

    body = Client().get("/readyz").json()

    assert body["degraded"] is False
    assert body["checks"]["kafka"]["ok"] is True
    assert body["checks"]["kafka"]["detail"] == "1 of 2 reachable"

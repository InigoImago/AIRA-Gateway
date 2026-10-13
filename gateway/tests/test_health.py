import aira_gateway.routes.health as health_module
from aira_common.counters import CountersUnavailable
from aira_common.health import CheckResult


def test_healthz(client) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ready(client, monkeypatch) -> None:
    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)

    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"]["ok"] is True
    assert body["checks"]["kafka"]["ok"] is True


def test_readyz_not_ready(client, monkeypatch) -> None:
    async def all_fail(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=False, detail=f"{host}:{port} unreachable")

    monkeypatch.setattr(health_module, "check_tcp", all_fail)

    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["kafka"]["ok"] is False
    assert "unreachable" in body["checks"]["kafka"]["detail"]


def test_readyz_reports_the_counter_store_without_failing_on_it(client, monkeypatch) -> None:
    """Redis being down degrades the gateway (ADR-0008); it must not take the instance out of
    service, or a cache outage becomes an outage."""

    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)

    class _Down:
        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            raise CountersUnavailable("connection refused")

    client.app.state.counters = _Down()

    resp = client.get("/readyz")

    assert resp.status_code == 200, "degraded is not the same as not ready"
    body = resp.json()
    assert body["status"] == "ready"
    assert body["degraded"] is True
    assert body["checks"]["counters"]["ok"] is False
    # The detail has to say what it costs, not merely that something is down.
    assert "per-instance" in body["checks"]["counters"]["detail"]


def test_readyz_reports_a_reachable_counter_store(client, monkeypatch) -> None:
    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)

    class _Up:
        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            return 1

    client.app.state.counters = _Up()

    body = client.get("/readyz").json()

    assert body["degraded"] is False
    assert body["checks"]["counters"]["ok"] is True


def test_readyz_says_so_when_no_counter_store_is_configured(client, monkeypatch) -> None:
    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)
    client.app.state.counters = None

    body = client.get("/readyz").json()

    assert body["checks"]["counters"]["detail"] == "not configured"
    assert body["checks"]["counters"]["required"] is False


def test_readyz_names_the_features_that_are_running_on_a_fallback(client, monkeypatch) -> None:
    """A probe answers "is the store reachable now". It cannot tell you that rate limiting has
    been serving from per-instance buckets for the last hour — for that, the features have to
    say so themselves."""

    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)

    class _Up:
        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            return 1

    client.app.state.counters = _Up()  # the probe succeeds …
    client.app.state.degradation.degraded("rate limiting", "per-instance buckets")

    body = client.get("/readyz").json()

    # … and the endpoint still reports the degradation, because a reachable store does not undo
    # what the feature already fell back to.
    assert body["degraded"] is True
    assert body["fallbacks"] == {"rate limiting": "per-instance buckets"}
    assert body["checks"]["counters"]["ok"] is True


def test_readyz_reports_no_fallbacks_when_everything_is_working(client, monkeypatch) -> None:
    async def all_ok(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
        return CheckResult(name=name, ok=True)

    monkeypatch.setattr(health_module, "check_tcp", all_ok)

    class _Up:
        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            return 1

    client.app.state.counters = _Up()

    body = client.get("/readyz").json()

    assert body["degraded"] is False
    assert body["fallbacks"] == {}


async def test_a_gateway_without_kafka_is_ready_on_postgres_alone() -> None:
    """`security.py` accepts a single-node install with Kafka switched off; readiness then probed
    `localhost:9092` for ever, and an orchestrator never routed to the instance."""
    import socket

    from fastapi.testclient import TestClient

    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        host, port = listener.getsockname()
        app = create_app(
            GatewaySettings(postgres_host=host, postgres_port=port, kafka_bootstrap_servers="")
        )
        with TestClient(app) as client:
            response = client.get("/readyz")

    assert response.status_code == 200, response.text
    assert response.json()["checks"]["kafka"] == {
        "ok": True,
        "detail": "not configured",
        "required": False,
    }


async def test_an_ipv6_broker_is_reached() -> None:
    import socket

    from fastapi.testclient import TestClient

    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    with socket.socket(socket.AF_INET6) as broker, socket.socket() as database:
        broker.bind(("::1", 0))
        broker.listen(8)
        database.bind(("127.0.0.1", 0))
        database.listen(8)
        db_host, db_port = database.getsockname()
        app = create_app(
            GatewaySettings(
                postgres_host=db_host,
                postgres_port=db_port,
                kafka_bootstrap_servers=f"[::1]:{broker.getsockname()[1]}",
            )
        )
        with TestClient(app) as client:
            response = client.get("/readyz")

    assert response.json()["checks"]["kafka"]["ok"] is True, response.text

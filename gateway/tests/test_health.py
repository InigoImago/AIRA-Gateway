import aira_gateway.routes.health as health_module
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

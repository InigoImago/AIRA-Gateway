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

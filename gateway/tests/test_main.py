from fastapi import FastAPI


def test_main_exposes_asgi_app() -> None:
    from aira_gateway.main import app

    assert isinstance(app, FastAPI)
    assert app.title == "aira-gateway"

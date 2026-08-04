"""Production ASGI entry point: ``uvicorn aira_gateway.main:app``."""

from __future__ import annotations

from aira_gateway.app import create_app

app = create_app()

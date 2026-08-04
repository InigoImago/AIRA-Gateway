"""URL routes for the health app."""

from __future__ import annotations

from django.urls import path

from aira_management.apps.health import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
]

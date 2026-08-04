"""API v1 URL routes."""

from __future__ import annotations

from django.urls import path

from aira_management.apps.api.views import MeView

urlpatterns = [
    path("me", MeView.as_view(), name="me"),
]

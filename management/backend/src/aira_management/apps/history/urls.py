"""Configuration history routes (`FRD-632`)."""

from __future__ import annotations

from django.urls import path

from aira_management.apps.history.views import ConfigurationChangesView

urlpatterns = [
    path(
        "configuration-changes/",
        ConfigurationChangesView.as_view(),
        name="configuration-changes",
    ),
]

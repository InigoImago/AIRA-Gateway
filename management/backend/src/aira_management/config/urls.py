"""Root URL configuration."""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("", include("aira_management.apps.health.urls")),
    path("api/v1/", include("aira_management.apps.api.urls")),
    path("api/v1/", include("aira_management.apps.usecases.urls")),
    path("api/v1/", include("aira_management.apps.catalog.urls")),
    path("api/v1/", include("aira_management.apps.anomalies.urls")),
    path("api/v1/", include("aira_management.apps.smoketests.urls")),
    path("api/v1/", include("aira_management.apps.directory.urls")),
]

"""Directory search routes (FRD-209)."""

from __future__ import annotations

from django.urls import path

from aira_management.apps.directory.views import DirectorySearchView

urlpatterns = [
    path("directory/", DirectorySearchView.as_view(), name="directory-search"),
]

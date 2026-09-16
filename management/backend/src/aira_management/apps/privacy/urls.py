"""The privacy notice's routes (`FRD-625`)."""

from __future__ import annotations

from django.urls import path

from aira_management.apps.privacy.views import AcknowledgementView, PrivacyNoticeView

urlpatterns = [
    path("privacy-notice", PrivacyNoticeView.as_view(), name="privacy-notice"),
    path(
        "privacy-notice/acknowledgements",
        AcknowledgementView.as_view(),
        name="privacy-notice-acknowledgements",
    ),
]

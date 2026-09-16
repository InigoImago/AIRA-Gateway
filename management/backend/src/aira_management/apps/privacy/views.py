"""The privacy notice API (`FRD-625` FR-3–FR-5): read it, and say you have.

Any signed-in person may do both, and only for themselves: the notice is addressed to everybody who
uses the console, and an acknowledgement is a statement about its author. Whether the window must
open is decided here (`schedule`) and sent with the notice, so the console asks rather than works
it out.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_management.apps.privacy import notice
from aira_management.apps.privacy.edition import EDITION
from aira_management.apps.privacy.models import NoticeAcknowledgement
from aira_management.apps.privacy.schedule import (
    Acknowledged,
    NoticeMode,
    acknowledgement_due,
)
from aira_management.apps.privacy.texts import LANGUAGES
from aira_management.config.runtime import get_settings
from aira_management.rbac import role_definitions


class NoticeChanged(APIException):
    """The reader acknowledged a version that is no longer the notice."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "The privacy notice changed while it was open. Read the current edition and acknowledge "
        "that one."
    )
    default_code = "notice_changed"


def _language(raw: Any) -> str:
    """A language this installation has, refused by name otherwise."""
    code = str(raw or "").strip().lower()
    if code not in LANGUAGES:
        raise ValidationError({"language": [f"One of {', '.join(LANGUAGES)}."]})
    return code


def _installation() -> notice.Installation:
    """What this installation's notice states: its settings, and the roles as they are now."""
    return notice.Installation.of(get_settings(), role_definitions())


def _state(user: Any, version: str, now: dt.datetime) -> dict[str, Any]:
    """Whether ``user`` must acknowledge ``version`` now, and when they last did."""
    mode = NoticeMode(get_settings().privacy_notice_mode)
    rows = NoticeAcknowledgement.objects.filter(user=user)
    current = rows.filter(version=version).first()
    last = Acknowledged(current.version, current.last_at) if current else None
    due = acknowledgement_due(mode, version, last, rows.exists(), now)
    return {
        "mode": str(mode),
        "due": str(due) if due else None,
        "acknowledged_at": current.last_at.isoformat() if current else None,
    }


class PrivacyNoticeView(APIView):
    """``GET /api/v1/privacy-notice?language=`` — the notice, and whether it must be acknowledged.

    Without ``language`` the browser's ``Accept-Language`` decides, then the installation's
    default. An explicit language the installation has no text for is refused by name, never
    swapped for another: whoever asked for it would read a notice they did not ask for.
    """

    def get(self, request: Request) -> Response:
        settings = get_settings()
        if "language" in request.query_params:
            code = _language(request.query_params.get("language"))
        else:
            code = notice.negotiate(
                request.headers.get("Accept-Language", ""), settings.privacy_default_language
            )
        installation = _installation()
        version = notice.version(installation)
        return Response(
            {
                **notice.render(code, installation),
                "version": version,
                "edition": EDITION,
                "languages": notice.languages(),
                **_state(request.user, version, dt.datetime.now(dt.UTC)),
            }
        )


class AcknowledgementView(APIView):
    """``POST /api/v1/privacy-notice/acknowledgements`` — ``{version, language}``.

    Refuses a version that is not the current one (`409`): acknowledging a text the reader was not
    shown would be a record of something that did not happen.
    """

    def post(self, request: Request) -> Response:
        if not isinstance(request.data, dict):
            raise ValidationError("A JSON object.")
        code = _language(request.data.get("language"))
        version = str(request.data.get("version") or "").strip()
        if not version:
            raise ValidationError({"version": ["The version of the notice that was read."]})
        current = notice.version(_installation())
        if version != current:
            raise NoticeChanged()
        now = dt.datetime.now(dt.UTC)
        # A signed-in person: `IsAuthenticated` refused everybody else before this ran.
        user: Any = request.user
        try:
            with transaction.atomic():
                row, created = NoticeAcknowledgement.objects.get_or_create(
                    user=user,
                    version=version,
                    defaults={"language": code, "last_at": now},
                )
        except IntegrityError:
            # A second window of the same person acknowledged between the read and the write.
            row, created = (
                NoticeAcknowledgement.objects.get(user=user, version=version),
                False,
            )
        if not created:
            row.language = code
            row.last_at = now
            row.save(update_fields=["language", "last_at"])
        return Response(
            {
                "version": row.version,
                "language": row.language,
                "first_at": row.first_at.isoformat(),
                "last_at": row.last_at.isoformat(),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

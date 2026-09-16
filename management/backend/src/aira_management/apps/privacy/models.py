"""Who acknowledged which edition of the privacy notice, and when (`FRD-625` FR-5).

One row per person and edition. A later acknowledgement of the same edition moves ``last_at``
instead of adding a row: the monthly rule needs the latest, accountability (Art. 5(2) DSGVO) needs
the first, and `always` mode would otherwise write a row on every start of the console.

The row is the person's own data and goes with their account — it is recorded in the register it
acknowledges (`activities.ACKNOWLEDGEMENT`).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class NoticeAcknowledgement(models.Model):
    """One person's acknowledgement of one edition of the notice."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="privacy_acknowledgements",
    )
    #: `notice.version`: the edition date and a digest of every language as rendered here.
    version = models.CharField(max_length=64)
    #: The language it was read in.
    language = models.CharField(max_length=8)
    first_at = models.DateTimeField(auto_now_add=True)
    last_at = models.DateTimeField()

    class Meta:
        ordering = ["-last_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "version"], name="uq_privacy_ack_user_version"),
        ]

    def __str__(self) -> str:
        return f"{self.user} acknowledged {self.version}"

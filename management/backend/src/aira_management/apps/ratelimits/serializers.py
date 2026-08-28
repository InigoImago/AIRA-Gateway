"""Serializer + validation for rate limits (FRD-405)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.ratelimits.models import RateLimit


class RateLimitSerializer(serializers.ModelSerializer[RateLimit]):
    class Meta:
        model = RateLimit
        fields = [
            "id",
            "scope",
            "subject",
            "limit_rpm",
            "burst",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # **No scope names a person any more** (2026-08-14), so a subject sent with one is
        # meaningless — cleared rather than stored, or the uniqueness constraint would let the same
        # rule be created twice under two different empty-ish subjects. Cleared rather than refused
        # because a client that still sends the field is asking for something harmless, and an
        # error would be about a word rather than about the rule.
        subject = ""
        attrs["subject"] = subject

        # **Nothing more is checked here, and that is the correction.** This carried
        # `if burst and burst < 1: raise` under a comment saying *"a burst below the sustained rate
        # is almost certainly a mistake"* — and both halves were wrong.
        #
        # The branch could not fire: `burst` is a `PositiveIntegerField`, so it is a non-negative
        # integer, and `burst != 0 and burst < 1` describes no integer at all. A guard that cannot
        # fail is the shape this project breaks every new guard on purpose to avoid, and this one
        # had never been broken.
        #
        # The rule the comment stated would have been worse than absent, because a burst **below**
        # the per-minute figure is the ordinary way to shape traffic rather than a mistake: 600/min
        # with a burst of 5 means ten a second, at most five at once, which is exactly what a
        # bucket is for. The gateway's own tests configure that pair deliberately. Refusing it
        # would have taken away the control and left the rate.
        #
        # What actually bounds the field is on the model — `PositiveIntegerField` plus
        # `MaxValueValidator(1_000_000)` — where a bound belongs, and `limit_rpm`'s
        # `MinValueValidator(1)` is what stops a limit of zero switching a use case off by
        # accident.
        return attrs

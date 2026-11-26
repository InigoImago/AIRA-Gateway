"""One row per change to the configuration the gateway enforces (`FRD-632`)."""

from __future__ import annotations

from django.db import models


class ConfigurationChange(models.Model):
    """Who changed what, when, and from what to what.

    One row per configuration event Management sends — every change that reaches the gateway passes
    through one (`aira_management.apps.usecases.events`) — written in the same transaction as the
    change and its outbox row, so a change that rolled back left no history and a history row never
    describes a change that did not happen.

    By key rather than by foreign key: the record of a deleted use case, key or role must outlive
    it. Kept for `AIRA_CONFIG_HISTORY_DAYS`, then deleted by the relay. Readable only by the roles
    that may see people (`incident.investigate`), because it names them in plain text.
    """

    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    #: The person signed in to the console who made it; empty for a change no person made — a seed,
    #: a migration, a command.
    actor = models.CharField(max_length=150, blank=True, default="")
    #: Which process made it when no person did: `seed`, `migrate`, …
    process = models.CharField(max_length=32, blank=True, default="")
    event_type = models.CharField(max_length=64)
    #: What kind of thing changed (`object` of the log line): use_case, membership, …
    kind = models.CharField(max_length=32, db_index=True)
    #: What was done: created, updated, deleted, granted, revoked, retired, purged.
    action = models.CharField(max_length=16)
    #: The thing's own key on its topic — a slug, a key prefix, an id; `slug|username` for a
    #: membership, `slug|group` for a group grant.
    object_key = models.CharField(max_length=512, db_index=True)
    use_case = models.CharField(max_length=64, blank=True, default="", db_index=True)
    #: The person it is about, where it is about one: the member, the key's owner.
    person = models.CharField(max_length=150, blank=True, default="")
    #: The state before and after, as the gateway was told it. `None` before a creation, and where
    #: the state before is not known — a change older than this history and its outbox.
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    #: The names of the fields whose values differ.
    fields_changed = models.JSONField(default=list, blank=True)
    #: The trace of the request that made it, to find its other records by.
    trace_id = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["-changed_at", "-id"]

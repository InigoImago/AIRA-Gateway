"""Roles an installation stores: what they may do, and which group confers them (`FRD-614`).

**A table of meanings, never of people** (`ADR-0025`). Who holds a role is decided by Keycloak
group membership and nowhere else; a row here says what holding it means. The Global Administrator
and IT Security are not stored at all — they are fixed in code so that no table can lock anybody
out. IT Steuerung is stored because its permissions may be changed; its group still comes from
`AIRA_ROLE_GROUPS`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q, UniqueConstraint


class StoredRole(models.Model):
    """One role this installation defines, or IT Steuerung's changed set."""

    slug = models.SlugField(max_length=64, unique=True)
    label = models.CharField(max_length=120)
    #: The one Keycloak group that confers this role. Empty for IT Steuerung, whose group is
    #: configuration (`AIRA_ROLE_GROUPS`).
    group_path = models.CharField(max_length=255, blank=True, default="")
    #: Permission names. Read through `aira_common.permissions.parse_permissions`, so an unknown or
    #: reserved name confers nothing even if a row carries it.
    permissions = models.JSONField(default=list)
    #: A built-in role cannot be deleted.
    builtin = models.BooleanField(default=False)
    created_by = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        constraints = [
            # One group, one role (`FRD-614` FR-3): a group that conferred two roles would make
            # "what does this group mean here" a question with two answers.
            UniqueConstraint(
                fields=["group_path"], condition=~Q(group_path=""), name="uq_role_group_path"
            ),
        ]

    def __str__(self) -> str:
        return self.slug


class RoleChange(models.Model):
    """Who changed which role, when, and from what to what (`FRD-614` FR-6).

    By slug rather than a foreign key: the record of a deleted role must outlive it.
    """

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACTION_CHOICES = [(CREATED, "Created"), (UPDATED, "Updated"), (DELETED, "Deleted")]

    role_slug = models.CharField(max_length=64, db_index=True)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    actor = models.CharField(max_length=150)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    #: Label, group and permissions before and after. `None` before a creation and after a
    #: deletion.
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-at", "-id"]

"""The roles API (`FRD-614` FR-3–FR-6): read with `role.read`, write with `role.manage`.

Every write happens in one transaction with its change-log row and its `aira.roles` event, so the
gateway never learns of a change that rolled back, and no change happens without its record.
"""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.utils.text import slugify
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from aira_common.directory import DirectoryUnavailable
from aira_common.permissions import (
    CATALOGUE,
    GRANTABLE,
    STRICT_DATA_RESERVED,
    Permission,
    RoleDefinition,
    grantable,
)
from aira_common.roles import Role
from aira_management.apps.directory.service import build_directory
from aira_management.apps.roles.models import RoleChange, StoredRole
from aira_management.apps.usecases.events import emit
from aira_management.config.runtime import get_settings
from aira_management.pagination import ConsolePagination
from aira_management.rbac import requires, role_definitions, role_groups

#: The longest label a role may carry; also the column's length.
LABEL_MAX = 120
#: The longest group path a role may be bound to: its column's width, which only PostgreSQL
#: enforces — as a `DataError` on the save, past every check below.
GROUP_PATH_MAX = StoredRole._meta.get_field("group_path").max_length or 255


class ChangedConcurrently(APIException):
    """Two administrators changed one role at once, and the other change was saved first."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Somebody changed this role at the same moment. Reload it and try again."
    default_code = "conflict"


class DirectoryCannotVerify(APIException):
    """The group could not be checked, so it is not bound (`FRD-614` FR-4)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "The directory could not be asked whether this group exists, so it is not bound. "
        "A group nobody could verify is refused rather than trusted."
    )
    default_code = "directory_unavailable"


def _ordered(permissions: frozenset[Permission]) -> list[str]:
    """In the catalogue's order, so a list and its change-log entry read the same way."""
    return [str(permission) for permission in CATALOGUE if permission in permissions]


def _present(role: RoleDefinition) -> dict[str, Any]:
    return {
        "slug": role.slug,
        "label": role.label,
        "group_paths": list(role.group_paths),
        "permissions": _ordered(role.permissions),
        "builtin": role.builtin,
        "fixed": role.fixed,
    }


def _snapshot(row: StoredRole) -> dict[str, Any]:
    return {"label": row.label, "group_path": row.group_path, "permissions": list(row.permissions)}


def _event(row: StoredRole) -> dict[str, Any]:
    return {
        "slug": row.slug,
        "label": row.label,
        "group_path": row.group_path,
        "permissions": list(row.permissions),
        "builtin": row.builtin,
    }


def _permissions(raw: Any) -> list[str]:
    """The requested set, refused whole if one name is wrong — a typo that granted nothing
    silently is the failure this project keeps recording."""
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        raise ValidationError({"permissions": ["A list of permission names."]})
    known = {str(permission) for permission in Permission}
    unknown = sorted({name for name in raw if name not in known})
    if unknown:
        raise ValidationError({"permissions": [f"Unknown permission: {', '.join(unknown)}."]})
    wanted = frozenset(Permission(name) for name in raw)
    reserved = wanted - GRANTABLE
    if reserved:
        raise ValidationError(
            {
                "permissions": [
                    f"{', '.join(sorted(str(p) for p in reserved))} is held by the Global "
                    "Administrator alone and cannot be given to a role."
                ]
            }
        )
    # Strict data mode (`FRD-627` FR-2): what sees a person stays with the two incident roles. Asked
    # of the setting at the write, as the read asks it, so a grant refused here is never one the
    # read would have dropped silently.
    withheld = wanted - grantable(strict_data=get_settings().strict_data_mode)
    if withheld:
        raise ValidationError(
            {
                "permissions": [
                    f"{', '.join(sorted(str(p) for p in withheld))} is held by Global "
                    "Administrator and IT Security only while strict data mode is on "
                    "(AIRA_STRICT_DATA_MODE), and cannot be given to another role."
                ]
            }
        )
    return _ordered(wanted)


def _label(raw: Any) -> str:
    label = str(raw or "").strip()
    if not label:
        raise ValidationError({"label": ["A role needs a name."]})
    if len(label) > LABEL_MAX:
        raise ValidationError({"label": [f"At most {LABEL_MAX} characters."]})
    return label


def _verified_group(raw: Any, *, keep: str | None = None) -> str:
    """A group path that exists in Keycloak and confers no other role (`FRD-614` FR-3, FR-4).

    ``keep`` is the path the role already has, which it may keep without a second check.
    """
    path = str(raw or "").strip()
    if not path.startswith("/") or path == "/":
        raise ValidationError(
            {"group_path": ["A full Keycloak group path, such as '/finance/controlling'."]}
        )
    if len(path) > GROUP_PATH_MAX:
        raise ValidationError({"group_path": [f"At most {GROUP_PATH_MAX} characters."]})
    if path == keep:
        return path
    configured = {p for paths in role_groups().values() for p in paths}
    if path in configured or StoredRole.objects.filter(group_path=path).exists():
        raise ValidationError({"group_path": [f"'{path}' already confers a role."]})
    directory = build_directory()
    if directory is None:
        raise DirectoryCannotVerify()
    try:
        exists = directory.group_exists(path)
    except DirectoryUnavailable as exc:
        raise DirectoryCannotVerify() from exc
    if not exists:
        raise ValidationError({"group_path": [f"Keycloak has no group '{path}'."]})
    return path


def _body(request: Request) -> dict[str, Any]:
    """The request's JSON object. Any other shape is the caller's mistake, answered as one."""
    if not isinstance(request.data, dict):
        raise ValidationError("A JSON object.")
    return dict(request.data)


def _record(action_name: str, slug: str, actor: Any, before: Any, after: Any) -> None:
    RoleChange.objects.create(
        role_slug=slug,
        action=action_name,
        actor=actor.get_username(),
        before=before,
        after=after,
    )


class RoleViewSet(viewsets.ViewSet):
    """``/api/v1/roles/`` — the roles, the catalogue they are made of, and their change log."""

    lookup_field = "slug"
    lookup_value_regex = "[-a-z0-9_]+"

    def get_permissions(self) -> list[Any]:
        if self.action in ("list", "retrieve", "catalogue", "changes"):
            return [IsAuthenticated(), requires(Permission.ROLE_READ)()]
        return [IsAuthenticated(), requires(Permission.ROLE_MANAGE)()]

    def _definition(self, slug: str) -> RoleDefinition:
        for role in role_definitions():
            if role.slug == slug:
                return role
        raise NotFound("No such role.")

    def list(self, request: Request) -> Response:  # noqa: ARG002
        return Response([_present(role) for role in role_definitions()])

    def retrieve(self, request: Request, slug: str | None = None) -> Response:  # noqa: ARG002
        return Response(_present(self._definition(str(slug))))

    @action(detail=False, methods=["get"])
    def catalogue(self, request: Request) -> Response:  # noqa: ARG002
        """Every permission with its area and label, for the console's checkboxes (FR-10).

        ``grantable`` is whether a stored role may hold it now, and ``strict_data_reserved``
        whether strict data mode is what withholds it (`FRD-627` FR-12) — so the console can say
        why, rather than restating which permissions the mode reserves.
        """
        strict = get_settings().strict_data_mode
        allowed = grantable(strict_data=strict)
        return Response(
            [
                {
                    "name": str(permission),
                    "area": info.area,
                    "label": info.label,
                    "sensitive": info.sensitive,
                    "reserved": info.reserved,
                    "grantable": permission in allowed,
                    "strict_data_reserved": strict and permission in STRICT_DATA_RESERVED,
                }
                for permission, info in CATALOGUE.items()
            ]
        )

    @action(detail=False, methods=["get"])
    def changes(self, request: Request) -> Response:
        paginator = ConsolePagination()
        rows = RoleChange.objects.all()
        slug = str(request.query_params.get("role", "")).strip()
        if slug:
            rows = rows.filter(role_slug=slug)
        page = paginator.paginate_queryset(rows, request, view=self) or []
        return paginator.get_paginated_response(
            [
                {
                    "id": row.pk,
                    "role": row.role_slug,
                    "action": row.action,
                    "actor": row.actor,
                    "at": row.at.isoformat(),
                    "before": row.before,
                    "after": row.after,
                }
                for row in page
            ]
        )

    @action(detail=False, methods=["post"], url_path="check-group")
    def check_group(self, request: Request) -> Response:
        """Whether a group may be bound, asked before saving; the save asks again."""
        path = _verified_group(_body(request).get("group_path"))
        return Response({"group_path": path, "exists": True})

    def create(self, request: Request) -> Response:
        data = _body(request)
        label = _label(data.get("label"))
        slug = slugify(label)[:64]
        if not slug:
            raise ValidationError({"label": ["A name with at least one letter or digit."]})
        if slug in {str(role) for role in Role} or StoredRole.objects.filter(slug=slug).exists():
            raise ValidationError({"label": [f"A role called '{label}' exists already."]})
        permissions = _permissions(data.get("permissions", []))
        group_path = _verified_group(data.get("group_path"))
        try:
            with transaction.atomic():
                row = StoredRole.objects.create(
                    slug=slug,
                    label=label,
                    group_path=group_path,
                    permissions=permissions,
                    created_by=request.user.get_username(),
                )
                _record(RoleChange.CREATED, slug, request.user, None, _snapshot(row))
                emit("role.upserted", _event(row))
        except IntegrityError as exc:
            # Two administrators saving at once: the constraint decides, and the refusal names the
            # value that was taken — the name or the group — rather than guessing.
            if StoredRole.objects.filter(slug=slug).exists():
                raise ValidationError(
                    {"label": [f"A role called '{label}' exists already."]}
                ) from exc
            raise ValidationError(
                {"group_path": [f"'{group_path}' already confers a role."]}
            ) from exc
        return Response(_present(self._definition(slug)), status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, slug: str | None = None) -> Response:
        data = _body(request)
        role = self._definition(str(slug))
        if role.fixed:
            raise PermissionDenied(f"{role.label} is fixed and cannot be changed.")
        row = StoredRole.objects.filter(slug=role.slug).first()
        if row is None:
            # IT Steuerung before anybody changed it: its row is created on the first change, with
            # the set it holds until then — a change that names no permissions changes none.
            row = StoredRole(
                slug=role.slug,
                label=role.label,
                builtin=True,
                permissions=_ordered(role.permissions),
            )
        before = (
            _snapshot(row)
            if row.pk
            else {
                "label": role.label,
                "group_path": "",
                "permissions": _ordered(role.permissions),
            }
        )
        if "permissions" in data:
            row.permissions = _permissions(data.get("permissions"))
        if not role.builtin:
            if "label" in data:
                row.label = _label(data.get("label"))
            if "group_path" in data:
                row.group_path = _verified_group(data.get("group_path"), keep=row.group_path)
        elif {"label", "group_path"} & set(data):
            raise ValidationError(
                "A built-in role keeps its name, and its group comes from AIRA_ROLE_GROUPS."
            )
        try:
            with transaction.atomic():
                row.save()
                _record(RoleChange.UPDATED, row.slug, request.user, before, _snapshot(row))
                emit("role.upserted", _event(row))
        except IntegrityError as exc:
            if row.builtin:
                # A built-in role's group is configuration, so its only unique value is the slug of
                # the row its first change creates — which a concurrent first change created too.
                raise ChangedConcurrently() from exc
            raise ValidationError({"group_path": ["That group already confers a role."]}) from exc
        return Response(_present(self._definition(row.slug)))

    def destroy(self, request: Request, slug: str | None = None) -> Response:
        role = self._definition(str(slug))
        if role.builtin:
            raise PermissionDenied(f"{role.label} is built in and cannot be deleted.")
        row = StoredRole.objects.get(slug=role.slug)
        with transaction.atomic():
            before = _snapshot(row)
            row.delete()
            _record(RoleChange.DELETED, role.slug, request.user, before, None)
            emit("role.removed", {"slug": role.slug})
        return Response(status=status.HTTP_204_NO_CONTENT)

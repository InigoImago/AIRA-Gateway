"""Stored roles and their change log (`FRD-614`), with IT Steuerung's default set."""

from __future__ import annotations

from typing import Any

from django.db import migrations, models
from django.db.models import Q

#: IT Steuerung's set as `aira_common.permissions.IT_STEUERUNG_DEFAULT` defines it today, written
#: out so this migration means the same thing whatever that constant becomes.
IT_STEUERUNG_DEFAULT = [
    "usecase.read_all",
    "usecase.read_retired",
    "report.read_all",
    "trace.read_all",
    "content_read.read",
    "anomaly.read_all",
    "role.read",
]


def _store_it_steuerung(apps: Any, schema_editor: Any) -> None:  # noqa: ARG001
    stored_role = apps.get_model("roles", "StoredRole")
    stored_role.objects.get_or_create(
        slug="it-steuerung",
        defaults={
            "label": "IT Steuerung",
            "group_path": "",
            "permissions": IT_STEUERUNG_DEFAULT,
            "builtin": True,
        },
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="StoredRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("label", models.CharField(max_length=120)),
                ("group_path", models.CharField(blank=True, default="", max_length=255)),
                ("permissions", models.JSONField(default=list)),
                ("builtin", models.BooleanField(default=False)),
                ("created_by", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["slug"]},
        ),
        migrations.AddConstraint(
            model_name="storedrole",
            constraint=models.UniqueConstraint(
                condition=~Q(group_path=""), fields=("group_path",), name="uq_role_group_path"
            ),
        ),
        migrations.CreateModel(
            name="RoleChange",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("role_slug", models.CharField(db_index=True, max_length=64)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("created", "Created"),
                            ("updated", "Updated"),
                            ("deleted", "Deleted"),
                        ],
                        max_length=16,
                    ),
                ),
                ("actor", models.CharField(max_length=150)),
                ("at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("before", models.JSONField(blank=True, null=True)),
                ("after", models.JSONField(blank=True, null=True)),
            ],
            options={"ordering": ["-at", "-id"]},
        ),
        migrations.RunPython(_store_it_steuerung, migrations.RunPython.noop),
    ]

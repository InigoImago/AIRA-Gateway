"""Acknowledgements of the privacy notice (`FRD-625`)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NoticeAcknowledgement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.CharField(max_length=64)),
                ("language", models.CharField(max_length=8)),
                ("first_at", models.DateTimeField(auto_now_add=True)),
                ("last_at", models.DateTimeField()),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="privacy_acknowledgements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-last_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "version"), name="uq_privacy_ack_user_version"
                    )
                ],
            },
        ),
    ]

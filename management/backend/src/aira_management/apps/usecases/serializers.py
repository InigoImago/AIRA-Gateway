"""Serializers for use-cases (FRD-202)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.usecases.models import UseCase, UseCaseMembership


class UseCaseSerializer(serializers.ModelSerializer[UseCase]):
    class Meta:
        model = UseCase
        fields = ["slug", "name", "description", "processing_notes", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class MembershipSerializer(serializers.ModelSerializer[UseCaseMembership]):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UseCaseMembership
        fields = ["username", "role", "created_at"]


class AddMemberSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    role = serializers.ChoiceField(
        choices=[UseCaseMembership.ADMIN, UseCaseMembership.USER],
        default=UseCaseMembership.USER,
    )

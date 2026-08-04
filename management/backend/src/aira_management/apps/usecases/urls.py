"""Use-case API routes."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.usecases.views import UseCaseViewSet

router = DefaultRouter()
router.register("use-cases", UseCaseViewSet, basename="usecase")

urlpatterns = router.urls

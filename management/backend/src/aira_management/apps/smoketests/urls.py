"""Smoke-test routes (`FRD-504`)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.smoketests.views import (
    TestCaseViewSet,
    TestResultViewSet,
    TestRunViewSet,
    TestStatsViewSet,
)

router = DefaultRouter()
router.register("test-cases", TestCaseViewSet, basename="test-case")
router.register("test-runs", TestRunViewSet, basename="test-run")
router.register("test-results", TestResultViewSet, basename="test-result")
router.register("test-stats", TestStatsViewSet, basename="test-stat")

urlpatterns = router.urls

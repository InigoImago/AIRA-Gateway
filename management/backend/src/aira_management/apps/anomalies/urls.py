"""Anomaly-rule routes (FRD-500)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.anomalies.views import AnomalyRuleViewSet

router = DefaultRouter()
router.register("anomaly-rules", AnomalyRuleViewSet, basename="anomaly-rule")

urlpatterns = router.urls

"""Model catalog routes."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.catalog.views import ModelViewSet

router = DefaultRouter()
router.register("models", ModelViewSet, basename="model")

urlpatterns = router.urls

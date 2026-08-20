"""The installation budget's routes (`FRD-610`)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.budgets.views import InstallationBudgetViewSet

router = DefaultRouter()
router.register("installation-budgets", InstallationBudgetViewSet, basename="installation-budget")

urlpatterns = router.urls

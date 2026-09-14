"""The roles API's routes (`FRD-614`)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from aira_management.apps.roles.views import RoleViewSet

router = DefaultRouter()
router.register("roles", RoleViewSet, basename="role")

urlpatterns = router.urls

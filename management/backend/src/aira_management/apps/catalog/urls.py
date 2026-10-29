"""Model catalog routes."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from aira_management.apps.catalog.fallback import FallbackChainsView, FallbackChainView
from aira_management.apps.catalog.views import ModelReferenceView, ModelViewSet

router = DefaultRouter()
router.register("models", ModelViewSet, basename="model")

# Beside `models/`, not below it: a model's name is the whole rest of that path, so
# `models/reference/` would be the model called "reference".
urlpatterns = [
    path("model-reference/", ModelReferenceView.as_view(), name="model-reference"),
    # The installation's fallback chain per kind of request (`FRD-310`), beside the catalogue for
    # the same reason as the reference: below `models/` a kind would be read as a model's name.
    path("fallback-chains/", FallbackChainsView.as_view(), name="fallback-chains"),
    path("fallback-chains/<str:kind>/", FallbackChainView.as_view(), name="fallback-chain"),
    *router.urls,
]

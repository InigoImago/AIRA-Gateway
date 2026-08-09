from django.apps import AppConfig


class SmokeTestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aira_management.apps.smoketests"
    label = "smoketests"

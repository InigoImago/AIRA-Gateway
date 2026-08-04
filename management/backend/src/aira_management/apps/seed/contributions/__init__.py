"""Seed contributions. Importing this package registers all of them.

Later phases add modules here (use cases, budgets, anomaly rules, sample traffic) and
import them below so a single ``seed_demo`` run covers every feature.
"""

from aira_management.apps.seed.contributions import roles_and_users  # noqa: F401

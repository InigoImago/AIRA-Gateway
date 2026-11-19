"""Shared building blocks for AIRA components.

**The package's root imports nothing.** Every module is imported by its own name —
``aira_common.config``, ``aira_common.logging`` — so a process loads only what it uses. The image
healthcheck, ``python -m aira_common.healthcheck``, runs every fifteen seconds in every container,
inside the core the service itself runs on; a root that imported the settings, the OpenTelemetry
SDK and an HTTP client made each run of it a third of a second of that core's time.
"""

__version__ = "0.1.0"

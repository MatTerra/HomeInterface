"""Device backends: the only part of the app that talks to the outside world."""

from __future__ import annotations

import os
from typing import Any

from .base import Alert, Backend, Entity, Link
from .mock import MockBackend

__all__ = ["Alert", "Backend", "Entity", "Link", "MockBackend", "build_backend"]


def build_backend(config: dict[str, Any], *, entity_ids: list[str] | None = None,
                  labels: dict[str, str] | None = None, plan: Any = None) -> Backend:
    """Instantiate the backend named in ``config['backend']['kind']``.

    Secrets are read from the environment first (``HA_URL`` / ``HA_TOKEN``)
    so the committed config never has to carry a token.

    ``plan`` is the loaded :class:`~homeinterface.floorplan.FloorPlan`.  When
    ``backend.sync_registry`` is on, the Home Assistant backend uses it to
    reconcile HA's floors, areas and labels on every connect.
    """
    section = dict(config.get("backend") or {})
    kind = str(section.get("kind", "mock")).lower()

    if kind == "mock":
        return MockBackend(entity_ids, labels=labels, chaos=bool(section.get("chaos", False)))

    if kind in ("homeassistant", "home_assistant", "ha"):
        from .homeassistant import HomeAssistantBackend
        from .registry import registry_plan

        url = os.environ.get("HA_URL") or section.get("url")
        token = os.environ.get("HA_TOKEN") or section.get("token")
        if not url:
            raise ValueError("Home Assistant backend needs backend.url or $HA_URL")
        if not token:
            raise ValueError("Home Assistant backend needs backend.token or $HA_TOKEN")
        sync = bool(section.get("sync_registry", True))
        return HomeAssistantBackend(
            url,
            token,
            verify_ssl=bool(section.get("verify_ssl", True)),
            entity_filter=section.get("entity_filter"),
            registry=registry_plan(plan) if (sync and plan is not None) else None,
        )

    raise ValueError(f"unknown backend kind {kind!r}")

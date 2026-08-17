"""Device backend contract.

The UI never talks to Home Assistant (or MQTT, or anything else) directly.
It reads :class:`Entity` snapshots out of a :class:`Backend` and pushes
service calls back in.  Backends run their own I/O on a background thread and
publish immutable snapshots, so the render loop never blocks on the network.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Link(Enum):
    """Connection state of the backend, shown in the status bar."""

    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"

    @property
    def level(self) -> str:
        return {
            Link.ONLINE: "normal",
            Link.CONNECTING: "info",
            Link.DEGRADED: "caution",
            Link.OFFLINE: "warning",
        }[self]


@dataclass(frozen=True)
class Entity:
    """One controllable or observable thing, mirrored from the backend."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    updated: float = field(default_factory=time.monotonic)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def name(self) -> str:
        return str(self.attributes.get("friendly_name") or self.entity_id.split(".", 1)[-1])

    @property
    def available(self) -> bool:
        return self.state not in ("unavailable", "unknown", "")

    @property
    def is_on(self) -> bool:
        return self.state == "on"

    def number(self, key: str, default: float | None = None) -> float | None:
        """Read a numeric attribute (or the state itself when ``key`` is ``"state"``)."""
        raw = self.state if key == "state" else self.attributes.get(key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    @property
    def level(self) -> str:
        """Semantic colour level for this entity's current state."""
        if not self.available:
            return "inop"
        if self.domain in ("binary_sensor", "alarm_control_panel") and self.is_on:
            device_class = self.attributes.get("device_class")
            if device_class in ("smoke", "gas", "safety", "problem", "co"):
                return "warning"
            if device_class in ("moisture", "door", "window"):
                return "caution"
        return "on" if self.is_on else "off"


@dataclass
class Alert:
    """A line for the ECAM-style message strip."""

    key: str
    text: str
    level: str = "caution"  # caution | warning | info
    raised: float = field(default_factory=time.monotonic)


class Backend(ABC):
    """Thread-safe snapshot store plus a command channel."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entities: dict[str, Entity] = {}
        self._link = Link.OFFLINE
        self._alerts: dict[str, Alert] = {}
        self._last_error: str | None = None
        self._listeners: list[Callable[[Entity], None]] = []
        self._revision = 0

    # -- lifecycle -------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Begin background I/O. Must return immediately."""

    @abstractmethod
    def stop(self) -> None:
        """Tear down background I/O. Must be idempotent."""

    @abstractmethod
    def call(
        self, domain: str, service: str, entity_id: str | list[str] | None = None, **data: Any
    ) -> None:
        """Queue a service call. Fire-and-forget; failures surface as alerts.

        ``entity_id`` may be a list, which targets every entity in one call -
        that is how zone-wide commands stay a single round-trip.
        """

    # -- reads (UI thread) ----------------------------------------------
    def snapshot(self) -> dict[str, Entity]:
        with self._lock:
            return dict(self._entities)

    def get(self, entity_id: str) -> Entity | None:
        with self._lock:
            return self._entities.get(entity_id)

    def by_domain(self, *domains: str) -> list[Entity]:
        with self._lock:
            return [e for e in self._entities.values() if e.domain in domains]

    @property
    def link(self) -> Link:
        with self._lock:
            return self._link

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def revision(self) -> int:
        """Bumped on every state change; lets screens skip redundant work."""
        with self._lock:
            return self._revision

    def alerts(self) -> list[Alert]:
        order = {"warning": 0, "caution": 1, "info": 2}
        with self._lock:
            items = list(self._alerts.values())
        items.sort(key=lambda a: (order.get(a.level, 3), a.raised))
        return items

    # -- writes (backend thread) ----------------------------------------
    def _publish(self, entity: Entity) -> None:
        with self._lock:
            previous = self._entities.get(entity.entity_id)
            if previous is not None and previous.state == entity.state and previous.attributes == entity.attributes:
                return
            self._entities[entity.entity_id] = entity
            self._revision += 1
            listeners = list(self._listeners)
        for listener in listeners:
            listener(entity)

    def _publish_many(self, entities: list[Entity]) -> None:
        for entity in entities:
            self._publish(entity)

    def _drop(self, entity_id: str) -> None:
        with self._lock:
            if self._entities.pop(entity_id, None) is not None:
                self._revision += 1

    def _set_link(self, link: Link, error: str | None = None) -> None:
        with self._lock:
            self._link = link
            self._last_error = error
            self._revision += 1

    def raise_alert(self, key: str, text: str, level: str = "caution") -> None:
        with self._lock:
            self._alerts[key] = Alert(key, text, level)
            self._revision += 1

    def clear_alert(self, key: str) -> None:
        with self._lock:
            if self._alerts.pop(key, None) is not None:
                self._revision += 1

    def on_change(self, listener: Callable[[Entity], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    # -- convenience wrappers used by widgets ----------------------------
    def toggle(self, entity_id: str) -> None:
        domain = entity_id.split(".", 1)[0]
        service_domain = domain if domain in ("light", "switch", "fan", "input_boolean", "cover") else "homeassistant"
        if domain == "cover":
            entity = self.get(entity_id)
            service = "close_cover" if entity and entity.is_on else "open_cover"
            self.call("cover", service, entity_id)
            return
        self.call(service_domain, "toggle", entity_id)

    def set_brightness(self, entity_id: str, pct: float) -> None:
        pct = max(0.0, min(100.0, pct))
        if pct <= 0:
            self.call("light", "turn_off", entity_id)
        else:
            self.call("light", "turn_on", entity_id, brightness_pct=round(pct))

    def set_temperature(self, entity_id: str, celsius: float) -> None:
        self.call("climate", "set_temperature", entity_id, temperature=round(celsius, 1))

    def set_cover_position(self, entity_id: str, pct: float) -> None:
        self.call("cover", "set_cover_position", entity_id, position=round(max(0.0, min(100.0, pct))))

    # -- group operations (zones) ----------------------------------------
    def group_state(self, entity_ids: list[str]) -> tuple[int, int]:
        """``(on, total)`` over the entities that exist and are available."""
        known = [e for e in (self.get(i) for i in entity_ids) if e is not None and e.available]
        return sum(1 for e in known if e.is_on), len(known)

    def set_group(self, entity_ids: list[str], on: bool) -> None:
        """Turn a whole group on or off.

        Issued per domain rather than per entity so one service call covers
        every light, then every switch, and so on - a zone with a dozen
        members must not become a dozen round-trips.
        """
        by_domain: dict[str, list[str]] = {}
        for entity_id in entity_ids:
            by_domain.setdefault(entity_id.split(".", 1)[0], []).append(entity_id)
        for domain, ids in by_domain.items():
            if domain == "cover":
                self.call("cover", "open_cover" if on else "close_cover", ids)
            elif domain in ("light", "switch", "fan", "input_boolean"):
                self.call(domain, "turn_on" if on else "turn_off", ids)
            else:
                self.call("homeassistant", "turn_on" if on else "turn_off", ids)

    def toggle_group(self, entity_ids: list[str]) -> None:
        """Any-on -> all off, otherwise all on. The usual master-switch rule."""
        on, _total = self.group_state(entity_ids)
        self.set_group(entity_ids, on == 0)

    def set_group_brightness(self, entity_ids: list[str], pct: float) -> None:
        lights = [i for i in entity_ids if i.startswith("light.")]
        if not lights:
            return
        pct = max(0.0, min(100.0, pct))
        if pct <= 0:
            self.call("light", "turn_off", lights)
        else:
            self.call("light", "turn_on", lights, brightness_pct=round(pct))

    def set_group_temperature(self, entity_ids: list[str], celsius: float) -> None:
        climates = [i for i in entity_ids if i.startswith("climate.")]
        if climates:
            self.call("climate", "set_temperature", climates, temperature=round(celsius, 1))

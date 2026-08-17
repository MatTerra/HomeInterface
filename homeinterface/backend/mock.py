"""Simulated backend.

Lets the whole interface be developed, demoed and screenshotted with no
Home Assistant instance in reach.  Sensors drift, lights respond to commands
instantly, and an optional fault injector exercises the caution/warning
paths.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Any, Iterable

from .base import Backend, Entity, Link

TICK = 0.25


def _default_attributes(entity_id: str, label: str | None) -> dict[str, Any]:
    domain = entity_id.split(".", 1)[0]
    attrs: dict[str, Any] = {"friendly_name": label or entity_id.split(".", 1)[-1].replace("_", " ").title()}
    if domain == "light":
        attrs["brightness"] = 180
        attrs["supported_color_modes"] = ["brightness"]
    elif domain == "climate":
        attrs.update(temperature=22.0, current_temperature=22.0, hvac_modes=["off", "cool", "heat"])
    elif domain == "cover":
        attrs["current_position"] = 100
    elif domain == "sensor":
        attrs.setdefault("unit_of_measurement", "°C")
    return attrs


def _default_state(entity_id: str) -> str:
    domain = entity_id.split(".", 1)[0]
    return {
        "light": "off",
        "switch": "off",
        "fan": "off",
        "cover": "open",
        "binary_sensor": "off",
        "climate": "cool",
        "lock": "locked",
        "sensor": "22.0",
    }.get(domain, "off")


class MockBackend(Backend):
    """In-memory home that behaves plausibly."""

    def __init__(
        self,
        entity_ids: Iterable[str] | None = None,
        *,
        labels: dict[str, str] | None = None,
        chaos: bool = False,
        seed: int = 7,
    ) -> None:
        super().__init__()
        self._rng = random.Random(seed)
        self._chaos = chaos
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        labels = labels or {}
        ids = list(entity_ids or [])
        if not ids:
            ids = [
                "light.living_room",
                "light.kitchen",
                "switch.garden_pump",
                "climate.living_room",
                "sensor.outdoor_temperature",
                "sensor.house_power",
                "binary_sensor.front_door",
            ]
        # House-wide readouts the overview screen expects, seeded first so
        # their realistic ranges beat the generic per-domain defaults even
        # when the same id also appears as a device on the floor plan.
        for entity_id, state, unit in (
            ("sensor.house_power", "1420", "W"),
            ("sensor.house_energy_today", "8.4", "kWh"),
            ("sensor.outdoor_temperature", "24.6", "°C"),
            ("sensor.outdoor_humidity", "58", "%"),
            ("sensor.water_pressure", "3.1", "bar"),
        ):
            self._publish(
                Entity(
                    entity_id,
                    state,
                    {
                        "friendly_name": labels.get(
                            entity_id, entity_id.split(".", 1)[-1].replace("_", " ").title()
                        ),
                        "unit_of_measurement": unit,
                    },
                )
            )
        for entity_id in ids:
            if self.get(entity_id) is not None:
                continue
            self._publish(
                Entity(
                    entity_id=entity_id,
                    state=_default_state(entity_id),
                    attributes=_default_attributes(entity_id, labels.get(entity_id)),
                )
            )

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._set_link(Link.ONLINE)
        self._thread = threading.Thread(target=self._run, name="mock-backend", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._set_link(Link.OFFLINE)

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            self._drift()

    def _drift(self) -> None:
        t = time.monotonic() - self._t0
        for entity in self.by_domain("sensor"):
            base = entity.attributes.get("_base")
            if base is None:
                base = entity.number("state", 20.0) or 20.0
                attrs = dict(entity.attributes, _base=base)
            else:
                attrs = entity.attributes
            span = abs(base) * 0.06 + 0.4
            value = base + math.sin(t / 11.0 + hash(entity.entity_id) % 7) * span
            value += self._rng.uniform(-span, span) * 0.15
            digits = 0 if abs(base) > 200 else 1
            self._publish(Entity(entity.entity_id, f"{value:.{digits}f}", attrs))

        for entity in self.by_domain("climate"):
            target = entity.number("temperature", 22.0) or 22.0
            current = entity.number("current_temperature", target) or target
            current += (target - current) * 0.02 + self._rng.uniform(-0.02, 0.02)
            self._publish(
                Entity(entity.entity_id, entity.state, dict(entity.attributes, current_temperature=round(current, 1)))
            )

        if self._chaos and self._rng.random() < 0.004:
            doors = self.by_domain("binary_sensor")
            if doors:
                victim = self._rng.choice(doors)
                self._publish(
                    Entity(victim.entity_id, "on" if victim.state == "off" else "off", victim.attributes)
                )

    # -- commands --------------------------------------------------------
    def call(
        self, domain: str, service: str, entity_id: str | list[str] | None = None, **data: Any
    ) -> None:
        if entity_id is None:
            return
        if isinstance(entity_id, list):
            for one in entity_id:
                self.call(domain, service, one, **data)
            return
        entity = self.get(entity_id)
        if entity is None:
            self.raise_alert("mock.unknown", f"UNKNOWN ENTITY {entity_id}", "caution")
            return
        state, attrs = entity.state, dict(entity.attributes)

        if service == "toggle":
            state = "off" if state == "on" else "on"
        elif service in ("turn_on", "open_cover"):
            state = "on" if domain != "cover" else "open"
        elif service in ("turn_off", "close_cover"):
            state = "off" if domain != "cover" else "closed"
        elif service == "set_temperature":
            attrs["temperature"] = float(data.get("temperature", attrs.get("temperature", 22.0)))
        elif service == "set_cover_position":
            position = float(data.get("position", 100))
            attrs["current_position"] = position
            state = "open" if position > 0 else "closed"
        elif service == "set_hvac_mode":
            state = str(data.get("hvac_mode", state))

        if "brightness_pct" in data:
            attrs["brightness"] = round(float(data["brightness_pct"]) * 2.55)
            state = "on" if attrs["brightness"] > 0 else "off"
        if domain == "light" and state == "on" and not attrs.get("brightness"):
            attrs["brightness"] = 180

        self._publish(Entity(entity_id, state, attrs))

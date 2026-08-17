"""Translate a floor plan into the Home Assistant registry shape.

Home Assistant organises things as **floor > area > entity**, plus *labels*
that cut across areas.  The plan maps onto that as:

===============  ==========================  ================================
plan             Home Assistant              why
===============  ==========================  ================================
``Floor``        floor                       one storey, one HA floor
``Zone``         area                        the zone is the unit you operate
``Room``         label                       keeps per-room granularity alive
===============  ==========================  ================================

A room that belongs to no zone still needs somewhere to live, so it becomes
an area of its own *and* carries its own label - otherwise the hall lamp
would end up in no area at all, which is exactly the disorganisation this
exists to prevent.

A zone that spans storeys can only sit on one HA floor (areas have a single
parent), so it takes the floor of its first member and that is reported as a
note, not an error.

This module is pure data: it computes what *should* be true and nothing else.
Applying it - creating what is missing, leaving foreign labels alone - is
:meth:`HomeAssistantBackend._sync_registry`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..floorplan import FloorPlan


@dataclass(frozen=True)
class AreaSpec:
    """One desired area, and the floor it should hang under."""

    name: str
    floor: str


@dataclass(frozen=True)
class EntitySpec:
    """Where one entity belongs: exactly one area, at least one label."""

    entity_id: str
    area: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class RegistryPlan:
    floors: tuple[str, ...] = ()
    areas: tuple[AreaSpec, ...] = ()
    labels: tuple[str, ...] = ()
    entities: tuple[EntitySpec, ...] = ()
    #: non-fatal observations worth showing the operator once
    notes: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.entities

    @property
    def owned_labels(self) -> frozenset[str]:
        """Labels this plan manages, so a sync can retire stale ones.

        Compared case-insensitively by the caller: HA label names are free
        text and "Sala" / "sala" are the same place to a human.
        """
        return frozenset(self.labels)


def registry_plan(plan: FloorPlan) -> RegistryPlan:
    """Compute the desired HA registry state for ``plan``."""
    floors: dict[str, None] = {}
    areas: dict[str, AreaSpec] = {}
    labels: dict[str, None] = {}
    entities: list[EntitySpec] = []
    notes: list[str] = []
    placed: set[str] = set()

    # A zone's area sits on the floor of its first member, decided up front so
    # every room of the zone agrees on the answer.
    zone_floor: dict[str, str] = {}
    for zone in plan.zones:
        rooms = plan.zone_rooms(zone)
        if not rooms:
            continue
        zone_floor[zone.id] = rooms[0][0].registry_floor
        if zone.spans_floors:
            notes.append(
                f"zone {zone.name!r} spans {len(zone.floor_ids)} floors; "
                f"its HA area sits on {zone_floor[zone.id]!r}"
            )

    for floor in plan.floors:
        floors.setdefault(floor.registry_floor, None)
        for room in floor.rooms:
            zone = plan.zone_of(floor.id, room.id)
            if zone is not None:
                area_name = zone.registry_area
                area_floor = zone_floor.get(zone.id, floor.registry_floor)
            else:
                area_name = room.name
                area_floor = floor.registry_floor
            areas.setdefault(area_name, AreaSpec(area_name, area_floor))
            labels.setdefault(room.registry_label, None)

            for device in floor.devices_in(room.id):
                if device.entity_id in placed:
                    continue  # a device on a shared boundary lands in one room only
                placed.add(device.entity_id)
                entities.append(EntitySpec(device.entity_id, area_name, (room.registry_label,)))

        strays = [d.entity_id for d in floor.devices if d.entity_id not in placed]
        if strays:
            notes.append(
                f"floor {floor.name!r}: {len(strays)} device(s) sit in no room "
                f"and were left unassigned ({', '.join(sorted(strays)[:3])}"
                f"{'...' if len(strays) > 3 else ''})"
            )

    return RegistryPlan(
        floors=tuple(floors),
        areas=tuple(areas.values()),
        labels=tuple(labels),
        entities=tuple(entities),
        notes=tuple(notes),
    )

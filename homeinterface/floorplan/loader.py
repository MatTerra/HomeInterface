"""Load a floor plan from YAML or JSON.

The schema is intentionally forgiving about ordering and optional fields but
strict about shapes: a bad polygon should fail at load with a message naming
the floor and room, not silently render as a dot.

Rectangular rooms may be written as ``rect: [x, y, w, h]`` instead of a
polygon - most real houses are mostly rectangles and the shorthand keeps the
file readable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .model import Device, Floor, FloorPlan, Opening, Point, Room, Wall, Zone, ZoneMember


class PlanError(ValueError):
    """Raised with a path-ish context string so bad files are easy to fix."""


def _point(value: Any, where: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PlanError(f"{where}: expected [x, y], got {value!r}")
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise PlanError(f"{where}: non-numeric coordinate {value!r}") from exc


def _polygon(data: dict[str, Any], where: str) -> list[Point]:
    if "rect" in data:
        rect = data["rect"]
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise PlanError(f"{where}: rect must be [x, y, w, h]")
        x, y, w, h = (float(v) for v in rect)
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    raw = data.get("polygon")
    if not raw:
        raise PlanError(f"{where}: needs 'polygon' or 'rect'")
    points = [_point(p, f"{where}.polygon[{i}]") for i, p in enumerate(raw)]
    if len(points) < 3:
        raise PlanError(f"{where}: polygon needs at least 3 points, got {len(points)}")
    if points[0] == points[-1] and len(points) > 3:
        points.pop()  # tolerate explicitly closed rings
    return points


def _room(data: dict[str, Any], where: str) -> Room:
    room_id = str(data.get("id") or "").strip()
    if not room_id:
        raise PlanError(f"{where}: room needs an 'id'")
    label_at = data.get("label_at")
    return Room(
        id=room_id,
        name=str(data.get("name") or room_id.replace("_", " ").title()),
        polygon=_polygon(data, f"{where}.{room_id}"),
        kind=str(data.get("kind") or data.get("type") or "room"),
        label_at=_point(label_at, f"{where}.{room_id}.label_at") if label_at else None,
        ha_label=data.get("ha_label"),
    )


def _device(data: dict[str, Any], where: str) -> Device:
    entity_id = str(data.get("entity_id") or data.get("id") or "").strip()
    if "." not in entity_id:
        raise PlanError(f"{where}: device entity_id must look like 'domain.object', got {entity_id!r}")
    return Device(
        entity_id=entity_id,
        at=_point(data.get("at"), f"{where}.{entity_id}.at"),
        kind=str(data.get("kind") or "auto"),
        label=data.get("label"),
        room=data.get("room"),
    )


def _wall(data: Any, where: str) -> Wall:
    if isinstance(data, (list, tuple)) and len(data) == 2:
        return Wall(_point(data[0], where), _point(data[1], where))
    if isinstance(data, dict):
        return Wall(
            _point(data.get("a"), f"{where}.a"),
            _point(data.get("b"), f"{where}.b"),
            float(data.get("thickness", 0.15)),
        )
    raise PlanError(f"{where}: wall must be [[x,y],[x,y]] or {{a:, b:, thickness:}}")


def _opening(data: dict[str, Any], where: str) -> Opening:
    return Opening(
        kind=str(data.get("kind") or "door"),
        at=_point(data.get("at"), f"{where}.at"),
        width=float(data.get("width", 0.9)),
        angle=float(data.get("angle", 0.0)),
        swing=float(data.get("swing", 1.0)),
    )


def _floor(data: dict[str, Any], index: int) -> Floor:
    floor_id = str(data.get("id") or f"floor_{index}")
    where = f"floors[{floor_id}]"
    return Floor(
        id=floor_id,
        name=str(data.get("name") or floor_id.replace("_", " ").title()),
        level=int(data.get("level", index)),
        short_name=data.get("short_name"),
        ha_floor=data.get("ha_floor"),
        rooms=[_room(r, f"{where}.rooms") for r in data.get("rooms") or []],
        walls=[_wall(w, f"{where}.walls[{i}]") for i, w in enumerate(data.get("walls") or [])],
        openings=[_opening(o, f"{where}.openings[{i}]") for i, o in enumerate(data.get("openings") or [])],
        devices=[_device(d, f"{where}.devices") for d in data.get("devices") or []],
    )


def _zone(data: dict[str, Any], index: int, floors: list[Floor]) -> Zone:
    """Parse one zone, resolving its room references against the floors.

    Members may be written as ``room_id`` (resolved when that id is unique
    across the whole plan) or as ``floor_id.room_id`` (always unambiguous).
    A reference that matches nothing, or matches rooms on several floors, is
    an error: a silently-empty zone would show up as a control menu that does
    nothing.
    """
    zone_id = str(data.get("id") or f"zone_{index}").strip()
    where = f"zones[{zone_id}]"

    by_floor = {f.id: {r.id for r in f.rooms} for f in floors}
    everywhere: dict[str, list[str]] = {}
    for floor in floors:
        for room in floor.rooms:
            everywhere.setdefault(room.id, []).append(floor.id)

    refs = data.get("rooms")
    if not refs:
        raise PlanError(f"{where}: needs a non-empty 'rooms' list")

    members: list[ZoneMember] = []
    for ref in refs:
        text = str(ref).strip()
        if "." in text:
            floor_id, _, room_id = text.partition(".")
            if floor_id not in by_floor:
                raise PlanError(f"{where}: unknown floor {floor_id!r} in {text!r}")
            if room_id not in by_floor[floor_id]:
                raise PlanError(f"{where}: floor {floor_id!r} has no room {room_id!r}")
            member = ZoneMember(floor_id, room_id)
        else:
            hits = everywhere.get(text, [])
            if not hits:
                raise PlanError(f"{where}: no room named {text!r} on any floor")
            if len(hits) > 1:
                raise PlanError(
                    f"{where}: room id {text!r} exists on floors {sorted(hits)} - "
                    f"qualify it as '<floor>.{text}'"
                )
            member = ZoneMember(hits[0], text)
        if member in members:
            raise PlanError(f"{where}: room {text!r} listed twice")
        members.append(member)

    return Zone(
        id=zone_id,
        name=str(data.get("name") or zone_id.replace("_", " ").title()),
        members=members,
        kind=str(data.get("kind") or "zone"),
        short_name=data.get("short_name"),
        ha_area=data.get("ha_area"),
    )


def plan_from_dict(data: dict[str, Any]) -> FloorPlan:
    if not isinstance(data, dict):
        raise PlanError("plan root must be a mapping")
    floors_raw = data.get("floors")
    if floors_raw is None:
        raise PlanError("plan needs a 'floors' list (use one entry for a single-storey house)")
    floors = [_floor(f, i) for i, f in enumerate(floors_raw)]
    ids = [f.id for f in floors]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise PlanError(f"duplicate floor ids: {sorted(duplicates)}")

    zones = [_zone(z, i, floors) for i, z in enumerate(data.get("zones") or [])]
    zone_ids = [z.id for z in zones]
    zone_dupes = {i for i in zone_ids if zone_ids.count(i) > 1}
    if zone_dupes:
        raise PlanError(f"duplicate zone ids: {sorted(zone_dupes)}")
    # a room in two zones would make "which menu controls it" undefined
    claimed: dict[tuple[str, str], str] = {}
    for zone in zones:
        for member in zone.members:
            key = (member.floor_id, member.room_id)
            if key in claimed:
                raise PlanError(
                    f"room {member.floor_id}.{member.room_id} is in both "
                    f"zone {claimed[key]!r} and zone {zone.id!r}"
                )
            claimed[key] = zone.id

    return FloorPlan(
        name=str(data.get("name") or "Home"),
        units=str(data.get("units") or "m"),
        floors=floors,
        zones=zones,
        meta=dict(data.get("meta") or {}),
    )


def load_plan(path: str | Path) -> FloorPlan:
    """Read a ``.yaml``/``.yml``/``.json`` plan file."""
    path = Path(path)
    if not path.exists():
        raise PlanError(f"floor plan not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise PlanError(f"{path}: {exc}") from exc
    try:
        return plan_from_dict(data)
    except PlanError as exc:
        raise PlanError(f"{path}: {exc}") from exc


def dump_plan(plan: FloorPlan) -> dict[str, Any]:
    """Serialise back to the on-disk shape (used by the SVG importer)."""
    return {
        "name": plan.name,
        "units": plan.units,
        "meta": plan.meta,
        "zones": [
            {
                "id": zone.id,
                "name": zone.name,
                "kind": zone.kind,
                **({"short_name": zone.short_name} if zone.short_name else {}),
                **({"ha_area": zone.ha_area} if zone.ha_area else {}),
                "rooms": [f"{m.floor_id}.{m.room_id}" for m in zone.members],
            }
            for zone in plan.zones
        ],
        "floors": [
            {
                "id": floor.id,
                "name": floor.name,
                "level": floor.level,
                **({"short_name": floor.short_name} if floor.short_name else {}),
                **({"ha_floor": floor.ha_floor} if floor.ha_floor else {}),
                "rooms": [
                    {
                        "id": room.id,
                        "name": room.name,
                        "kind": room.kind,
                        "polygon": [[round(x, 3), round(y, 3)] for x, y in room.polygon],
                        **({"label_at": [round(room.label_at[0], 3), round(room.label_at[1], 3)]}
                           if room.label_at else {}),
                        **({"ha_label": room.ha_label} if room.ha_label else {}),
                    }
                    for room in floor.rooms
                ],
                "walls": [
                    [[round(w.a[0], 3), round(w.a[1], 3)], [round(w.b[0], 3), round(w.b[1], 3)]]
                    for w in floor.walls
                ],
                "openings": [
                    {
                        "kind": o.kind,
                        "at": [round(o.at[0], 3), round(o.at[1], 3)],
                        "width": o.width,
                        "angle": o.angle,
                        "swing": o.swing,
                    }
                    for o in floor.openings
                ],
                "devices": [
                    {
                        "entity_id": d.entity_id,
                        "at": [round(d.at[0], 3), round(d.at[1], 3)],
                        **({"kind": d.kind} if d.kind != "auto" else {}),
                        **({"label": d.label} if d.label else {}),
                        **({"room": d.room} if d.room else {}),
                    }
                    for d in floor.devices
                ],
            }
            for floor in plan.floors
        ],
    }

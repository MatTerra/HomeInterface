"""Floor plan data model.

Coordinates are plan-space, in the unit declared by ``FloorPlan.units``
(metres by default), with **x to the right and y downward** - the same
handedness as the screen and as SVG, so importing a drawing needs no flip.

The plan is pure geometry plus entity references.  It knows nothing about
Home Assistant or pygame, which is what makes it shareable: hand the YAML to
someone else and it renders identically against their own entity ids.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

Point = tuple[float, float]


@dataclass(frozen=True)
class BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> Point:
        return ((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    @classmethod
    def around(cls, points: Iterable[Point]) -> "BBox":
        pts = list(points)
        if not pts:
            return cls(0.0, 0.0, 1.0, 1.0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return cls(min(xs), min(ys), max(xs), max(ys))

    def merged(self, other: "BBox") -> "BBox":
        return BBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def expanded(self, margin: float) -> "BBox":
        return BBox(self.min_x - margin, self.min_y - margin, self.max_x + margin, self.max_y + margin)


def polygon_area(points: list[Point]) -> float:
    """Shoelace area; always positive."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_centroid(points: list[Point]) -> Point:
    """Area centroid, falling back to the vertex mean for degenerate rings."""
    if len(points) < 3:
        return BBox.around(points).center
    cx = cy = signed = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        signed += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(signed) < 1e-9:
        return BBox.around(points).center
    signed *= 0.5
    return (cx / (6.0 * signed), cy / (6.0 * signed))


def _span(polygon: list[Point], along: int, cut: float, through: float) -> tuple[float, float] | None:
    """Extent of the polygon along one axis, on the line ``cut``.

    ``along`` is the index of the coordinate that varies (0 for a horizontal
    scan, 1 for a vertical one).  Returns the single interval that contains
    ``through``, or None when the line misses the shape there.

    This is what makes a label fit an L- or S-shaped room: the bounding box of
    such a room promises width the room does not actually have at the point
    where the text will sit.
    """
    other = 1 - along
    hits: list[float] = []
    n = len(polygon)
    for i in range(n):
        p1, p2 = polygon[i], polygon[(i + 1) % n]
        a, b = p1[other], p2[other]
        if (a > cut) != (b > cut):
            t = (cut - a) / (b - a)
            hits.append(p1[along] + t * (p2[along] - p1[along]))
    hits.sort()
    for i in range(0, len(hits) - 1, 2):
        lo, hi = hits[i], hits[i + 1]
        if lo <= through <= hi:
            return (lo, hi)
    return None


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Ray casting; boundary membership is unspecified but consistent."""
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


@dataclass(frozen=True)
class Device:
    """An entity pinned to a point on the plan."""

    entity_id: str
    at: Point
    kind: str = "auto"  # light | switch | climate | sensor | cover | lock | camera | auto
    label: str | None = None
    room: str | None = None

    @property
    def resolved_kind(self) -> str:
        return self.entity_id.split(".", 1)[0] if self.kind == "auto" else self.kind

    @property
    def display_label(self) -> str:
        return self.label or self.entity_id.split(".", 1)[-1].replace("_", " ").title()


@dataclass(frozen=True)
class Opening:
    """A door or window, drawn as a gap plus a swing arc."""

    kind: str  # door | window | opening
    at: Point
    width: float = 0.9
    angle: float = 0.0  # degrees, direction of the wall it sits in
    swing: float = 1.0  # +1 / -1, which side a door opens toward


@dataclass(frozen=True)
class Room:
    id: str
    name: str
    polygon: list[Point]
    kind: str = "room"
    label_at: Point | None = None
    #: name of the Home Assistant *label* this room maps to; defaults to ``name``
    ha_label: str | None = None

    @property
    def registry_label(self) -> str:
        return self.ha_label or self.name

    @property
    def centroid(self) -> Point:
        return self.label_at or polygon_centroid(self.polygon)

    @property
    def area(self) -> float:
        return polygon_area(self.polygon)

    @property
    def bbox(self) -> BBox:
        return BBox.around(self.polygon)

    def contains(self, point: Point) -> bool:
        return point_in_polygon(point, self.polygon)

    @property
    def label_extent(self) -> tuple[float, float]:
        """``(width, height)`` genuinely free *around* the label anchor.

        Measured through the anchor rather than taken from the bounding box,
        so a room shaped like an L or an S is not credited with space that
        belongs to its neighbour.

        The spans are made symmetric about the anchor because labels are drawn
        centred on it: a room whose anchor sits off to one side has only twice
        its shortest reach to play with, and crediting it the full span would
        push half the text through the far wall.

        Falls back to the bounding box when the anchor sits outside the ring,
        which a centroid can do on a strongly concave shape.
        """
        cx, cy = self.centroid
        box = self.bbox
        across = _span(self.polygon, 0, cy, cx)
        down = _span(self.polygon, 1, cx, cy)
        width = 2 * min(cx - across[0], across[1] - cx) if across else box.width
        height = 2 * min(cy - down[0], down[1] - cy) if down else box.height
        return (max(width, 0.0), max(height, 0.0))


@dataclass(frozen=True)
class Wall:
    a: Point
    b: Point
    thickness: float = 0.15

    @property
    def length(self) -> float:
        return math.hypot(self.b[0] - self.a[0], self.b[1] - self.a[1])


@dataclass
class Floor:
    id: str
    name: str
    level: int = 0
    rooms: list[Room] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    short_name: str | None = None
    #: name of the Home Assistant *floor* this storey maps to; defaults to ``name``
    ha_floor: str | None = None

    @property
    def tag(self) -> str:
        """Short label for the floor selector strip."""
        return self.short_name or self.name[:3].upper()

    @property
    def registry_floor(self) -> str:
        return self.ha_floor or self.name

    @property
    def bbox(self) -> BBox:
        boxes = [r.bbox for r in self.rooms]
        boxes += [BBox.around([w.a, w.b]) for w in self.walls]
        boxes += [BBox.around([d.at]) for d in self.devices]
        if not boxes:
            return BBox(0.0, 0.0, 10.0, 10.0)
        result = boxes[0]
        for box in boxes[1:]:
            result = result.merged(box)
        return result

    def room_at(self, point: Point) -> Room | None:
        """Smallest room containing the point, so nested areas resolve sanely."""
        hits = [r for r in self.rooms if r.contains(point)]
        return min(hits, key=lambda r: r.area) if hits else None

    def devices_in(self, room_id: str) -> list[Device]:
        rooms = {r.id: r for r in self.rooms}
        room = rooms.get(room_id)
        out = []
        for device in self.devices:
            if device.room == room_id or (device.room is None and room and room.contains(device.at)):
                out.append(device)
        return out


@dataclass(frozen=True)
class ZoneMember:
    """One room, qualified by its floor - zones may span storeys."""

    floor_id: str
    room_id: str


@dataclass
class Zone:
    """Several rooms treated as one controllable unit.

    A zone is a *logical* grouping laid over the geometry, not a change to
    it: the rooms keep their own polygons, areas and devices.  It exists so
    that "the social area" or "the bedroom wing" can be operated from a
    single control menu instead of room by room, and so a stairwell that
    physically occupies two floors can still read as one place.
    """

    id: str
    name: str
    members: list[ZoneMember] = field(default_factory=list)
    kind: str = "zone"
    short_name: str | None = None
    #: name of the Home Assistant *area* this zone maps to; defaults to ``name``
    ha_area: str | None = None

    @property
    def tag(self) -> str:
        return self.short_name or self.name[:3].upper()

    @property
    def registry_area(self) -> str:
        return self.ha_area or self.name

    @property
    def floor_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for member in self.members:
            seen.setdefault(member.floor_id, None)
        return list(seen)

    @property
    def spans_floors(self) -> bool:
        return len(self.floor_ids) > 1

    def room_ids_on(self, floor_id: str) -> list[str]:
        return [m.room_id for m in self.members if m.floor_id == floor_id]

    def contains(self, floor_id: str, room_id: str) -> bool:
        return any(m.floor_id == floor_id and m.room_id == room_id for m in self.members)


@dataclass
class FloorPlan:
    name: str = "Home"
    units: str = "m"
    floors: list[Floor] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.floors.sort(key=lambda f: f.level)

    @property
    def is_empty(self) -> bool:
        return not self.floors

    def floor(self, floor_id: str) -> Floor | None:
        return next((f for f in self.floors if f.id == floor_id), None)

    def index_of(self, floor_id: str) -> int:
        for i, f in enumerate(self.floors):
            if f.id == floor_id:
                return i
        return 0

    @property
    def entity_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for floor in self.floors:
            for device in floor.devices:
                seen.setdefault(device.entity_id, None)
        return list(seen)

    @property
    def labels(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for floor in self.floors:
            for device in floor.devices:
                if device.label:
                    out[device.entity_id] = device.label
        return out

    # -- zones -----------------------------------------------------------
    def zone(self, zone_id: str) -> Zone | None:
        return next((z for z in self.zones if z.id == zone_id), None)

    def zone_of(self, floor_id: str, room_id: str) -> Zone | None:
        """The zone a room belongs to, or None if it is controlled alone."""
        return next((z for z in self.zones if z.contains(floor_id, room_id)), None)

    def zones_on(self, floor_id: str) -> list[Zone]:
        return [z for z in self.zones if floor_id in z.floor_ids]

    def zone_rooms(self, zone: Zone) -> list[tuple[Floor, Room]]:
        """Resolve a zone's members to (floor, room) pairs, skipping strays."""
        out: list[tuple[Floor, Room]] = []
        for member in zone.members:
            floor = self.floor(member.floor_id)
            if floor is None:
                continue
            room = next((r for r in floor.rooms if r.id == member.room_id), None)
            if room is not None:
                out.append((floor, room))
        return out

    def zone_devices(self, zone: Zone) -> list[Device]:
        """Every device in every room of the zone, across all its floors."""
        out: list[Device] = []
        for floor, room in self.zone_rooms(zone):
            out.extend(floor.devices_in(room.id))
        return out

    def zone_area(self, zone: Zone) -> float:
        return sum(room.area for _floor, room in self.zone_rooms(zone))

    @property
    def common_bbox(self) -> BBox:
        """Union of every floor, so floors share one scale and stay aligned."""
        if not self.floors:
            return BBox(0.0, 0.0, 10.0, 10.0)
        result = self.floors[0].bbox
        for floor in self.floors[1:]:
            result = result.merged(floor.bbox)
        return result

"""Structural checks on the real house plan.

These started as throwaway scripts while the plan was being traced from a
photograph, and each of them caught something: a door opening into a wall, a
bathroom that severed the corridor, a 0.40 strip of floor belonging to no room,
two rooms sharing a name and therefore collapsing into one Home Assistant
label.  They live here so the next edit to ``config/floorplan.yaml`` cannot
quietly reintroduce any of it.

The plan is geometry, so the checks are geometric: sample it, probe it, and
assert the things a floor plan must be true about itself.
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import pytest

from homeinterface.floorplan import load_plan
from homeinterface.floorplan.model import Floor, Room, point_in_polygon

PLAN = Path(__file__).resolve().parents[1] / "config" / "floorplan.yaml"

#: room kinds that are outside the closed envelope; a door onto one of these is
#: an exterior door, and exterior doors legitimately have no room on one side
OUTDOOR = {"outdoor", "garden", "balcony", "terrace"}

#: how far to step off a wall when asking which room is on either side - wider
#: than half a wall (0.075) so the probe clears it, narrower than any room
PROBE = 0.30


@pytest.fixture(scope="module")
def floor() -> Floor:
    return load_plan(PLAN).floors[0]


def room_at(floor: Floor, point) -> Room | None:
    """Smallest room containing the point, or None for outside/wall."""
    hits = [r for r in floor.rooms if point_in_polygon(point, r.polygon)]
    return min(hits, key=lambda r: r.area) if hits else None


def sides_of(floor: Floor, opening) -> list[Room | None]:
    """The two rooms an opening separates, in no particular order."""
    rad = math.radians(opening.angle)
    nx, ny = -math.sin(rad), math.cos(rad)
    return [
        room_at(floor, (opening.at[0] + nx * PROBE * s, opening.at[1] + ny * PROBE * s))
        for s in (-1, 1)
    ]


# -- openings ---------------------------------------------------------------


def test_every_door_leads_somewhere(floor):
    """A door with no room on either side is a door drawn into a wall."""
    stranded = [o.at for o in floor.openings
                if o.kind == "door" and not any(sides_of(floor, o))]
    assert not stranded, f"doors touching no room at all: {stranded}"


def test_no_door_opens_onto_its_own_room(floor):
    """Same room both sides means the door sits inside a room, not in a wall."""
    silly = []
    for o in floor.openings:
        if o.kind != "door":
            continue
        a, b = sides_of(floor, o)
        if a is not None and a is b:
            silly.append((o.at, a.id))
    assert not silly, f"doors with the same room on both sides: {silly}"


def test_exterior_doors_are_only_where_the_plan_says_outside(floor):
    """An exterior door must front an outdoor room or the outside world."""
    for o in floor.openings:
        if o.kind != "door":
            continue
        rooms = [r for r in sides_of(floor, o) if r is not None]
        if len(rooms) == 2 and not any(r.kind in OUTDOOR for r in rooms):
            continue  # ordinary interior door
        # otherwise it is an exterior door, and must still serve some room
        assert rooms, f"exterior door at {o.at} serves nothing"


def test_every_room_can_be_entered(floor):
    """A room nobody can walk into is a modelling slip, not a room."""
    reachable = set()
    for o in floor.openings:
        if o.kind != "door":
            continue
        reachable.update(r.id for r in sides_of(floor, o) if r is not None)
    orphans = sorted({r.id for r in floor.rooms} - reachable)
    assert not orphans, f"rooms with no door: {orphans}"


def test_every_window_serves_a_room(floor):
    for o in floor.openings:
        if o.kind != "window":
            continue
        assert any(sides_of(floor, o)), f"window at {o.at} serves no room"


# -- rooms ------------------------------------------------------------------


def test_rooms_do_not_overlap(floor):
    """Overlapping rooms make 'which room did I tap' undefined."""
    clashes = set()
    x = 0.05
    while x < 17.0:
        y = 0.05
        while y < 14.0:
            inside = tuple(sorted(r.id for r in floor.rooms
                                  if point_in_polygon((x, y), r.polygon)))
            if len(inside) > 1:
                clashes.add(inside)
            y += 0.10
        x += 0.10
    assert not clashes, f"rooms overlapping: {sorted(clashes)}"


def test_no_floor_area_belongs_to_nobody(floor):
    """No void inside the house bigger than the walls that separate rooms.

    Sampled on a grid and then eroded twice, because the 0.15 gaps between
    room polygons are walls, not holes - without the erosion they all connect
    into one meaningless blob spanning the whole house.
    """
    step = 0.10
    cols, rows = int(16.75 / step), int(9.40 / step)

    def outside(i: int, j: int) -> bool:
        return room_at(floor, (0.20 + i * step, 0.20 + j * step)) is None

    grid = [[outside(i, j) for j in range(rows)] for i in range(cols)]
    for _ in range(2):
        grid = [
            [
                grid[i][j] and all(
                    0 <= i + a < cols and 0 <= j + b < rows and grid[i + a][j + b]
                    for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1))
                )
                for j in range(rows)
            ]
            for i in range(cols)
        ]

    seen = [[False] * rows for _ in range(cols)]
    voids = []
    for i in range(cols):
        for j in range(rows):
            if not grid[i][j] or seen[i][j]:
                continue
            queue, cells = deque([(i, j)]), 0
            seen[i][j] = True
            while queue:
                a, b = queue.popleft()
                cells += 1
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    na, nb = a + da, b + db
                    if 0 <= na < cols and 0 <= nb < rows and grid[na][nb] and not seen[na][nb]:
                        seen[na][nb] = True
                        queue.append((na, nb))
            voids.append((round(cells * step * step, 2), 0.20 + i * step, 0.20 + j * step))
    assert not voids, f"floor area in no room (m2, x, y): {voids}"


def test_room_names_are_unique(floor):
    """Two rooms with one name collapse into a single Home Assistant label."""
    names = [r.name.strip().lower() for r in floor.rooms]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate room names: {dupes}"


def test_room_ids_are_unique(floor):
    ids = [r.id for r in floor.rooms]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate room ids: {dupes}"


# -- walls and devices ------------------------------------------------------


def test_no_wall_runs_through_a_room(floor):
    """A wall with the same room on both sides is slicing that room in half."""
    offenders = []
    for wall in floor.walls:
        (ax, ay), (bx, by) = wall.a, wall.b
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        nx, ny = -(by - ay) / length, (bx - ax) / length
        for i in range(1, 200):
            t = i / 200.0
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            left = room_at(floor, (px + nx * 0.10, py + ny * 0.10))
            right = room_at(floor, (px - nx * 0.10, py - ny * 0.10))
            if left is not None and left is right:
                offenders.append((wall.a, wall.b, left.id))
                break
    assert not offenders, f"walls cutting through a room: {offenders}"


def test_every_device_sits_in_a_room(floor):
    """A device in no room gets no Home Assistant area and no room control."""
    homeless = [d.entity_id for d in floor.devices if room_at(floor, d.at) is None]
    assert not homeless, f"devices outside every room: {homeless}"


def test_entity_ids_are_unique(floor):
    ids = [d.entity_id for d in floor.devices]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate entity ids: {dupes}"

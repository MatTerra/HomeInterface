from __future__ import annotations

from pathlib import Path

import pytest

from homeinterface.floorplan.loader import (
    PlanError,
    dump_plan,
    load_plan,
    plan_from_dict,
)
from homeinterface.floorplan.model import (
    Device,
    Floor,
    FloorPlan,
    Room,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
)

EXAMPLE_PLAN = Path(__file__).resolve().parents[1] / "config" / "floorplan.example.yaml"


@pytest.fixture(scope="module")
def example_plan():
    return load_plan(EXAMPLE_PLAN)


def test_load_example_plan_counts(example_plan):
    assert len(example_plan.floors) == 2
    terreo = example_plan.floor("terreo")
    superior = example_plan.floor("superior")
    assert terreo is not None
    assert superior is not None
    assert len(terreo.rooms) == 7
    assert len(superior.rooms) == 6
    assert len(terreo.devices) == 8
    assert len(superior.devices) == 8


def test_floors_come_back_sorted_by_level(example_plan):
    levels = [f.level for f in example_plan.floors]
    assert levels == sorted(levels)
    assert example_plan.floors[0].id == "terreo"
    assert example_plan.floors[1].id == "superior"


def test_rect_shorthand_matches_explicit_polygon():
    rect_room = plan_from_dict({
        "floors": [{
            "id": "f",
            "rooms": [{"id": "r", "rect": [1.0, 2.0, 3.0, 4.0]}],
        }],
    }).floors[0].rooms[0]

    explicit_room = plan_from_dict({
        "floors": [{
            "id": "f",
            "rooms": [{
                "id": "r",
                "polygon": [[1.0, 2.0], [4.0, 2.0], [4.0, 6.0], [1.0, 6.0]],
            }],
        }],
    }).floors[0].rooms[0]

    assert rect_room.polygon == explicit_room.polygon


def test_polygon_area_centroid_point_in_polygon_on_known_square():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert polygon_area(square) == pytest.approx(100.0)
    assert polygon_centroid(square) == pytest.approx((5.0, 5.0))
    assert point_in_polygon((5.0, 5.0), square) is True
    assert point_in_polygon((-1.0, 5.0), square) is False
    assert point_in_polygon((15.0, 5.0), square) is False


def test_room_contains():
    room = Room(id="r", name="R", polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    assert room.contains((5.0, 5.0)) is True
    assert room.contains((20.0, 20.0)) is False


def test_floor_room_at_picks_smallest_containing_room():
    outer = Room(id="outer", name="Outer", polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    inner = Room(id="inner", name="Inner", polygon=[(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)])
    floor = Floor(id="f", name="F", rooms=[outer, inner])

    hit = floor.room_at((5.0, 5.0))
    assert hit is not None
    assert hit.id == "inner"

    # a point only inside the outer room resolves to the outer room
    hit_outer = floor.room_at((1.0, 1.0))
    assert hit_outer is not None
    assert hit_outer.id == "outer"

    # a point outside both resolves to nothing
    assert floor.room_at((50.0, 50.0)) is None


def test_floorplan_entity_ids_deduplicates():
    plan = FloorPlan(
        floors=[
            Floor(id="a", name="A", devices=[Device(entity_id="light.x", at=(0.0, 0.0))]),
            Floor(id="b", name="B", devices=[Device(entity_id="light.x", at=(1.0, 1.0)),
                                              Device(entity_id="light.y", at=(2.0, 2.0))]),
        ]
    )
    assert plan.entity_ids == ["light.x", "light.y"]


def test_planerror_polygon_with_fewer_than_3_points():
    with pytest.raises(PlanError):
        plan_from_dict({
            "floors": [{
                "id": "f",
                "rooms": [{"id": "r", "polygon": [[0.0, 0.0], [1.0, 1.0]]}],
            }],
        })


def test_planerror_device_entity_id_without_dot():
    with pytest.raises(PlanError):
        plan_from_dict({
            "floors": [{
                "id": "f",
                "devices": [{"entity_id": "notadomain", "at": [0.0, 0.0]}],
            }],
        })


def test_planerror_missing_floors_key():
    with pytest.raises(PlanError):
        plan_from_dict({"name": "no floors here"})


def test_round_trip_preserves_room_and_device_ids(example_plan):
    round_tripped = plan_from_dict(dump_plan(example_plan))
    for original, again in zip(example_plan.floors, round_tripped.floors):
        assert [r.id for r in original.rooms] == [r.id for r in again.rooms]
        assert [d.entity_id for d in original.devices] == [d.entity_id for d in again.devices]


# -- label extent (room shape vs. bounding box) -----------------------------


def test_label_extent_matches_bbox_for_a_rectangle():
    room = Room(id="r", name="R", polygon=[(0, 0), (4, 0), (4, 3), (0, 3)])
    assert room.label_extent == pytest.approx((4.0, 3.0))


def test_label_extent_is_tighter_than_bbox_on_an_s_shaped_room():
    """The jogged bedroom must not be credited with its widest slice."""
    room = Room(id="q2", name="Quarto 2", polygon=[
        (5.15, 0.15), (8.65, 0.15), (8.65, 2.225),
        (9.40, 2.225), (9.40, 4.30), (5.15, 4.30),
    ])
    width, height = room.label_extent
    assert width < room.bbox.width
    assert height < room.bbox.height


def test_label_extent_never_reaches_past_a_wall():
    """Text is centred on the anchor, so the budget must fit on BOTH sides."""
    room = Room(id="l", name="L", polygon=[
        (0, 0), (10, 0), (10, 2), (2, 2), (2, 6), (0, 6),
    ])
    cx, cy = room.centroid
    width, height = room.label_extent
    # the half-width each way stays inside the ring
    assert room.contains((cx - width / 2 + 1e-6, cy))
    assert room.contains((cx + width / 2 - 1e-6, cy))
    assert room.contains((cx, cy - height / 2 + 1e-6))
    assert room.contains((cx, cy + height / 2 - 1e-6))


def test_label_extent_falls_back_to_bbox_when_the_anchor_is_outside():
    """A label_at placed off the ring must not produce a negative budget."""
    room = Room(id="c", name="C", polygon=[
        (0, 0), (6, 0), (6, 2), (2, 2), (2, 4), (6, 4), (6, 6), (0, 6),
    ], label_at=(5.0, 3.0))
    width, height = room.label_extent
    assert width >= 0 and height >= 0

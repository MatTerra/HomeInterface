from __future__ import annotations

import math
from pathlib import Path

import pytest

from homeinterface.floorplan.svg_import import (
    Affine,
    ImportOptions,
    _normalise_origin,
    import_svg,
    parse_path,
    parse_transform,
)
from homeinterface.floorplan.model import FloorPlan, Room


SVG_HEADER = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "plan.svg"
    path.write_text(SVG_HEADER + body + "</svg>", encoding="utf-8")
    return path


def test_room_inside_floor_layer_yields_floor_and_room(tmp_path):
    svg = (
        '<g inkscape:label="floor:terreo:Térreo:0">'
        '<rect inkscape:label="room:sala:Sala" x="0" y="0" width="500" height="400"/>'
        '</g>'
    )
    path = _write(tmp_path, svg)
    plan = import_svg(path, ImportOptions(unit_scale=0.01))

    assert len(plan.floors) == 1
    floor = plan.floors[0]
    assert floor.id == "terreo"
    assert floor.name == "Térreo"
    assert floor.level == 0
    assert len(floor.rooms) == 1
    room = floor.rooms[0]
    assert room.name == "Sala"
    # 500x400 px at 0.01 units/px -> 5m x 4m -> 20 m^2
    assert room.area == pytest.approx(20.0, abs=0.01)


def test_parse_transform_translate_then_scale_composes_correctly():
    affine = parse_transform("translate(10,20) scale(2)")
    # SVG applies transforms left-to-right outermost-first: translate then scale
    # means a point is first scaled, then translated.
    x, y = affine.apply((1.0, 1.0))
    assert (x, y) == pytest.approx((12.0, 22.0))


def test_parse_transform_identity_for_empty():
    affine = parse_transform(None)
    assert affine == Affine()
    assert affine.apply((3.0, 4.0)) == pytest.approx((3.0, 4.0))


def test_parse_transform_scale_only():
    affine = parse_transform("scale(3)")
    assert affine.apply((2.0, 5.0)) == pytest.approx((6.0, 15.0))


def test_parse_path_closed_triangle():
    subpaths = parse_path("M0,0 L10,0 L10,10 Z")
    assert len(subpaths) == 1
    points, closed = subpaths[0]
    assert closed is True
    assert len(points) >= 3
    assert points[0] == pytest.approx((0.0, 0.0))
    assert points[1] == pytest.approx((10.0, 0.0))
    assert points[2] == pytest.approx((10.0, 10.0))


def test_device_circle_produces_device_at_centre(tmp_path):
    # A room anchored at the origin keeps _normalise_origin from shifting the
    # plan, so the device's plan-space position matches the circle's centre
    # directly.
    svg = (
        '<g inkscape:label="floor:terreo:Térreo:0">'
        '<rect inkscape:label="room:sala:Sala" x="0" y="0" width="1000" height="1000"/>'
        '<circle inkscape:label="device:light.sala:Luz" cx="100" cy="200" r="5"/>'
        '</g>'
    )
    path = _write(tmp_path, svg)
    plan = import_svg(path, ImportOptions(unit_scale=0.01))

    floor = plan.floors[0]
    assert len(floor.devices) == 1
    device = floor.devices[0]
    assert device.entity_id == "light.sala"
    assert device.label == "Luz"
    assert device.at == pytest.approx((1.0, 2.0), abs=0.01)


def test_two_floor_layers_produce_two_floors(tmp_path):
    svg = (
        '<g inkscape:label="floor:terreo:Térreo:0">'
        '<rect inkscape:label="room:sala:Sala" x="0" y="0" width="500" height="500"/>'
        '</g>'
        '<g inkscape:label="floor:superior:Superior:1">'
        '<rect inkscape:label="room:suite:Suite" x="0" y="0" width="500" height="500"/>'
        '</g>'
    )
    path = _write(tmp_path, svg)
    plan = import_svg(path, ImportOptions(unit_scale=0.01, min_room_area=0.01))

    assert len(plan.floors) == 2
    ids = {f.id for f in plan.floors}
    assert ids == {"terreo", "superior"}


def test_normalise_origin_shifts_plan_so_min_is_zero():
    room = Room(id="r", name="R", polygon=[(10.0, 20.0), (15.0, 20.0), (15.0, 25.0), (10.0, 25.0)])
    from homeinterface.floorplan.model import Floor

    floor = Floor(id="f", name="F", rooms=[room])
    plan = FloorPlan(floors=[floor])
    _normalise_origin(plan)

    box = plan.common_bbox
    assert box.min_x == pytest.approx(0.0, abs=1e-6)
    assert box.min_y == pytest.approx(0.0, abs=1e-6)

"""Import a floor plan drawn in Inkscape / Illustrator / QCAD-exported SVG.

The SVG is the *source drawing*; the YAML plan is the artefact the app reads.
Import once, commit the YAML, and hand-tune it - re-importing is cheap, so
treat the YAML as generated.

Naming convention
-----------------
Meaning is carried by each element's ``inkscape:label`` (Inkscape's "Label"
field, the ``Object Properties`` dialog) falling back to ``id``.  Fields are
colon-separated::

    floor:ground:Térreo:0        a <g> layer holding one storey
    room:living:Sala:living      a closed shape -> Room(id, name, kind)
    wall                         an open path -> wall segments
    door / window                a short segment -> an Opening
    device:light.sala:Luz Sala   a circle/small shape -> Device anchor

Anything unlabelled is ignored, so you can keep dimension lines, hatching and
title blocks in the drawing.

Geometry
--------
Only the flat subset of SVG is honoured: ``rect``, ``circle``, ``ellipse``,
``line``, ``polygon``, ``polyline`` and ``path``.  Beziers are flattened by
sampling; ``transform`` attributes (matrix/translate/scale/rotate) are
composed down the tree.  Arc commands (``A``) are approximated by their
chord, which is fine for architectural plans.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import Device, Floor, FloorPlan, Opening, Point, Room, Wall, polygon_centroid

SVG_NS = "{http://www.w3.org/2000/svg}"
INKSCAPE_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"
BEZIER_STEPS = 12


# ---------------------------------------------------------------------------
# affine transforms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Affine:
    """2x3 matrix ``[[a c e], [b d f]]`` in SVG's column order."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, p: Point) -> Point:
        x, y = p
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def then(self, other: "Affine") -> "Affine":
        """``self`` applied first, then ``other``."""
        return Affine(
            other.a * self.a + other.c * self.b,
            other.b * self.a + other.d * self.b,
            other.a * self.c + other.c * self.d,
            other.b * self.c + other.d * self.d,
            other.a * self.e + other.c * self.f + other.e,
            other.b * self.e + other.d * self.f + other.f,
        )


_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(m) for m in _NUM_RE.findall(text or "")]


def parse_transform(text: str | None) -> Affine:
    result = Affine()
    if not text:
        return result
    for name, args in _TRANSFORM_RE.findall(text):
        v = _numbers(args)
        if name == "matrix" and len(v) >= 6:
            step = Affine(*v[:6])
        elif name == "translate":
            step = Affine(e=v[0] if v else 0.0, f=v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            sx = v[0] if v else 1.0
            sy = v[1] if len(v) > 1 else sx
            step = Affine(a=sx, d=sy)
        elif name == "rotate":
            ang = math.radians(v[0] if v else 0.0)
            cos, sin = math.cos(ang), math.sin(ang)
            step = Affine(cos, sin, -sin, cos)
            if len(v) >= 3:
                step = Affine(e=-v[1], f=-v[2]).then(step).then(Affine(e=v[1], f=v[2]))
        elif name == "skewX":
            step = Affine(c=math.tan(math.radians(v[0] if v else 0.0)))
        elif name == "skewY":
            step = Affine(b=math.tan(math.radians(v[0] if v else 0.0)))
        else:
            continue
        # SVG applies the leftmost transform outermost
        result = step.then(result)
    return result


# ---------------------------------------------------------------------------
# path flattening
# ---------------------------------------------------------------------------

_PATH_TOKEN_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")


def _bezier3(p0: Point, p1: Point, p2: Point, p3: Point, steps: int) -> list[Point]:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        out.append((
            mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
            mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
        ))
    return out


def _bezier2(p0: Point, p1: Point, p2: Point, steps: int) -> list[Point]:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        out.append((
            mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0],
            mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1],
        ))
    return out


def parse_path(d: str) -> list[tuple[list[Point], bool]]:
    """Return ``[(points, closed), ...]`` subpaths in user units."""
    tokens: list[Any] = []
    for cmd, num in _PATH_TOKEN_RE.findall(d or ""):
        tokens.append(cmd if cmd else float(num))

    subpaths: list[tuple[list[Point], bool]] = []
    current: list[Point] = []
    cursor: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_ctrl: Point | None = None
    command = ""
    i = 0

    def flush(closed: bool) -> None:
        nonlocal current
        if len(current) >= 2:
            subpaths.append((current, closed))
        current = []

    while i < len(tokens):
        token = tokens[i]
        if isinstance(token, str):
            command = token
            i += 1
            if command in "Zz":
                flush(True)
                cursor = start
                continue
        relative = command.islower()
        op = command.upper()

        def take(n: int) -> list[float]:
            nonlocal i
            vals = [v for v in tokens[i:i + n] if isinstance(v, float)]
            i += n
            return vals

        if op == "M":
            v = take(2)
            if len(v) < 2:
                break
            cursor = (cursor[0] + v[0], cursor[1] + v[1]) if relative else (v[0], v[1])
            flush(False)
            current = [cursor]
            start = cursor
            command = "l" if relative else "L"  # implicit lineto for extra pairs
        elif op == "L":
            v = take(2)
            if len(v) < 2:
                break
            cursor = (cursor[0] + v[0], cursor[1] + v[1]) if relative else (v[0], v[1])
            current.append(cursor)
        elif op == "H":
            v = take(1)
            if not v:
                break
            cursor = (cursor[0] + v[0], cursor[1]) if relative else (v[0], cursor[1])
            current.append(cursor)
        elif op == "V":
            v = take(1)
            if not v:
                break
            cursor = (cursor[0], cursor[1] + v[0]) if relative else (cursor[0], v[0])
            current.append(cursor)
        elif op in ("C", "S"):
            n = 6 if op == "C" else 4
            v = take(n)
            if len(v) < n:
                break
            pts = [(v[k], v[k + 1]) for k in range(0, n, 2)]
            if relative:
                pts = [(cursor[0] + x, cursor[1] + y) for x, y in pts]
            if op == "C":
                c1, c2, end = pts
            else:
                c1 = (2 * cursor[0] - last_ctrl[0], 2 * cursor[1] - last_ctrl[1]) if last_ctrl else cursor
                c2, end = pts
            current.extend(_bezier3(cursor, c1, c2, end, BEZIER_STEPS))
            last_ctrl, cursor = c2, end
            continue
        elif op in ("Q", "T"):
            n = 4 if op == "Q" else 2
            v = take(n)
            if len(v) < n:
                break
            pts = [(v[k], v[k + 1]) for k in range(0, n, 2)]
            if relative:
                pts = [(cursor[0] + x, cursor[1] + y) for x, y in pts]
            if op == "Q":
                ctrl, end = pts
            else:
                ctrl = (2 * cursor[0] - last_ctrl[0], 2 * cursor[1] - last_ctrl[1]) if last_ctrl else cursor
                end = pts[0]
            current.extend(_bezier2(cursor, ctrl, end, BEZIER_STEPS))
            last_ctrl, cursor = ctrl, end
            continue
        elif op == "A":
            v = take(7)
            if len(v) < 7:
                break
            end = (cursor[0] + v[5], cursor[1] + v[6]) if relative else (v[5], v[6])
            current.append(end)  # chord approximation
            cursor = end
        else:
            i += 1
            continue
        last_ctrl = None

    flush(False)
    return subpaths


# ---------------------------------------------------------------------------
# element -> geometry
# ---------------------------------------------------------------------------

def _label(element: ET.Element) -> str:
    return (element.get(INKSCAPE_LABEL) or element.get("id") or "").strip()


def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def element_geometry(element: ET.Element) -> list[tuple[list[Point], bool]]:
    """Untransformed subpaths for a supported shape element."""
    tag = _tag(element)
    if tag == "rect":
        x = float(element.get("x", 0))
        y = float(element.get("y", 0))
        w = float(element.get("width", 0))
        h = float(element.get("height", 0))
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
    if tag in ("circle", "ellipse"):
        cx = float(element.get("cx", 0))
        cy = float(element.get("cy", 0))
        rx = float(element.get("r", element.get("rx", 0)))
        ry = float(element.get("r", element.get("ry", rx)))
        pts = [
            (cx + rx * math.cos(2 * math.pi * k / 16), cy + ry * math.sin(2 * math.pi * k / 16))
            for k in range(16)
        ]
        return [(pts, True)]
    if tag == "line":
        return [([(float(element.get("x1", 0)), float(element.get("y1", 0))),
                  (float(element.get("x2", 0)), float(element.get("y2", 0)))], False)]
    if tag in ("polygon", "polyline"):
        nums = _numbers(element.get("points", ""))
        pts = list(zip(nums[0::2], nums[1::2]))
        return [(pts, tag == "polygon")] if len(pts) >= 2 else []
    if tag == "path":
        return parse_path(element.get("d", ""))
    return []


@dataclass
class ImportOptions:
    #: plan units per SVG user unit. 100 px = 1 m -> 0.01
    unit_scale: float = 1.0
    #: drop polygons smaller than this many plan units squared
    min_room_area: float = 0.5
    units: str = "m"
    name: str = "Home"
    #: when the drawing has no floor:* layers, put everything on one storey
    default_floor: str = "ground"


def _split_label(label: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in label.split(":")]
    return parts[0].lower(), parts[1:]


def _iter_shapes(node: ET.Element, transform: Affine) -> Iterable[tuple[ET.Element, Affine]]:
    for child in node:
        child_tf = parse_transform(child.get("transform")).then(transform)
        if _tag(child) in ("g", "svg", "a", "switch"):
            kind, _ = _split_label(_label(child))
            if kind == "floor":
                continue  # nested storeys are handled by the caller
            yield from _iter_shapes(child, child_tf)
        else:
            yield child, child_tf


def _collect_floor(node: ET.Element, transform: Affine, floor: Floor, opts: ImportOptions) -> None:
    for element, tf in _iter_shapes(node, transform):
        label = _label(element)
        if not label:
            continue
        kind, fields = _split_label(label)
        subpaths = element_geometry(element)
        if not subpaths:
            continue
        scaled = [
            ([_scaled(tf.apply(p), opts.unit_scale) for p in pts], closed)
            for pts, closed in subpaths
        ]

        if kind == "room":
            room_id = fields[0] if fields else f"room_{len(floor.rooms) + 1}"
            name = fields[1] if len(fields) > 1 else room_id.replace("_", " ").title()
            room_kind = fields[2] if len(fields) > 2 else "room"
            for pts, _closed in scaled:
                if len(pts) < 3:
                    continue
                room = Room(id=room_id, name=name, polygon=pts, kind=room_kind)
                if room.area >= opts.min_room_area:
                    floor.rooms.append(room)
                    room_id = f"{room_id}_x"  # keep ids unique if a shape had holes
        elif kind == "wall":
            for pts, closed in scaled:
                ring = pts + [pts[0]] if closed and len(pts) > 2 else pts
                floor.walls.extend(Wall(ring[i], ring[i + 1]) for i in range(len(ring) - 1))
        elif kind in ("door", "window", "opening"):
            for pts, _closed in scaled:
                if len(pts) < 2:
                    continue
                a, b = pts[0], pts[-1]
                floor.openings.append(
                    Opening(
                        kind=kind,
                        at=((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                        width=math.hypot(b[0] - a[0], b[1] - a[1]),
                        angle=math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])),
                    )
                )
        elif kind == "device":
            if not fields or "." not in fields[0]:
                continue
            pts = scaled[0][0]
            floor.devices.append(
                Device(
                    entity_id=fields[0],
                    at=polygon_centroid(pts) if len(pts) >= 3 else pts[0],
                    label=fields[1] if len(fields) > 1 else None,
                )
            )


def _scaled(p: Point, k: float) -> Point:
    return (p[0] * k, p[1] * k)


def import_svg(path: str | Path, opts: ImportOptions | None = None) -> FloorPlan:
    opts = opts or ImportOptions()
    root = ET.parse(Path(path)).getroot()
    base = parse_transform(root.get("transform"))

    floors: list[Floor] = []
    layers = [
        (child, parse_transform(child.get("transform")).then(base))
        for child in root.iter()
        if _tag(child) == "g" and _split_label(_label(child))[0] == "floor"
    ]

    if layers:
        for index, (layer, tf) in enumerate(layers):
            _, fields = _split_label(_label(layer))
            floor_id = fields[0] if fields else f"floor_{index}"
            name = fields[1] if len(fields) > 1 else floor_id.replace("_", " ").title()
            level = int(fields[2]) if len(fields) > 2 and fields[2].lstrip("-").isdigit() else index
            floor = Floor(id=floor_id, name=name, level=level)
            _collect_floor(layer, tf, floor, opts)
            floors.append(floor)
    else:
        floor = Floor(id=opts.default_floor, name=opts.default_floor.title(), level=0)
        _collect_floor(root, base, floor, opts)
        floors.append(floor)

    plan = FloorPlan(name=opts.name, units=opts.units, floors=floors,
                     meta={"source": str(path), "unit_scale": opts.unit_scale})
    _normalise_origin(plan)
    return plan


def _normalise_origin(plan: FloorPlan) -> None:
    """Shift the whole plan so its top-left sits at (0, 0)."""
    box = plan.common_bbox
    dx, dy = -box.min_x, -box.min_y
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return
    for floor in plan.floors:
        floor.rooms = [
            Room(r.id, r.name, [(x + dx, y + dy) for x, y in r.polygon], r.kind,
                 (r.label_at[0] + dx, r.label_at[1] + dy) if r.label_at else None)
            for r in floor.rooms
        ]
        floor.walls = [Wall((w.a[0] + dx, w.a[1] + dy), (w.b[0] + dx, w.b[1] + dy), w.thickness)
                       for w in floor.walls]
        floor.openings = [Opening(o.kind, (o.at[0] + dx, o.at[1] + dy), o.width, o.angle, o.swing)
                          for o in floor.openings]
        floor.devices = [Device(d.entity_id, (d.at[0] + dx, d.at[1] + dy), d.kind, d.label, d.room)
                         for d in floor.devices]

"""Convert an SVG drawing into a HomeInterface floor plan YAML.

    python tools/svg2plan.py casa.svg -o config/floorplan.yaml --px-per-unit 100

Labelling convention (Inkscape "Label", or the element ``id``):

    floor:<id>:<Name>:<level>     a layer/group holding one storey
    room:<id>:<Name>:<kind>       a closed shape
    wall                          an open path or polyline
    door / window                 a short segment
    device:<entity_id>:<Label>    a circle or small shape

Run with ``--inspect`` first to see what the importer found before writing
anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homeinterface.floorplan.loader import dump_plan  # noqa: E402
from homeinterface.floorplan.svg_import import ImportOptions, import_svg  # noqa: E402


def describe(plan) -> str:
    lines = [f"{plan.name}  ({plan.units})", ""]
    for floor in plan.floors:
        box = floor.bbox
        lines.append(
            f"  [{floor.level:+d}] {floor.id:<14} {floor.name:<24} "
            f"{len(floor.rooms):>2} rooms  {len(floor.walls):>3} walls  "
            f"{len(floor.openings):>2} openings  {len(floor.devices):>2} devices"
        )
        lines.append(f"       extent {box.width:.2f} x {box.height:.2f} {plan.units}")
        for room in floor.rooms:
            lines.append(f"         · {room.id:<14} {room.name:<20} {room.area:6.2f} {plan.units}²")
        for device in floor.devices:
            lines.append(f"         @ {device.entity_id:<28} at ({device.at[0]:.2f}, {device.at[1]:.2f})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("svg", help="input SVG file")
    parser.add_argument("-o", "--out", help="output YAML (default: stdout)")
    parser.add_argument("--name", default="Home", help="plan name")
    parser.add_argument("--units", default="m", help="unit label for the plan (default: m)")
    parser.add_argument(
        "--px-per-unit", type=float, default=100.0,
        help="SVG user units per plan unit. 100 means 100px = 1 m (default: 100)",
    )
    parser.add_argument("--min-area", type=float, default=0.5, help="discard rooms below this area")
    parser.add_argument("--inspect", action="store_true", help="print a summary instead of YAML")
    args = parser.parse_args(argv)

    if args.px_per_unit <= 0:
        parser.error("--px-per-unit must be positive")

    plan = import_svg(
        args.svg,
        ImportOptions(
            unit_scale=1.0 / args.px_per_unit,
            min_room_area=args.min_area,
            units=args.units,
            name=args.name,
        ),
    )

    if args.inspect:
        print(describe(plan))
        return 0

    text = yaml.safe_dump(dump_plan(plan), sort_keys=False, allow_unicode=True, default_flow_style=None)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(describe(plan), file=sys.stderr)
        print(f"\nwrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

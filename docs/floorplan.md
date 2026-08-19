# Floor plan format

The house is one YAML document that knows nothing about pygame or Home
Assistant, so the same drawing renders identically against someone else's
entity ids. Terms used here (floor, room, zone, device) are defined in
[`CONTEXT.md`](../CONTEXT.md).

`FloorPlan` (`homeinterface/floorplan/model.py`) is pure geometry and
entity references — no pygame, no Home Assistant. Coordinates are in the
unit declared by `units` (metres by default), **x to the right, y
downward** (same handedness as the screen and as SVG, so importing a
drawing needs no flip). All floors share one coordinate system, so they
stay aligned when you flip between them — draw every storey as if stacked
on the one below it.

```yaml
name: Casa Exemplo
units: m

floors:
  - id: terreo            # stable id, referenced by devices' `room:` field
    name: Térreo
    short_name: TER        # 3-4 char tag shown on the floor selector strip
    level: 0                # sort key; 0 = ground, 1 = up, -1 = basement

    rooms:
      - {id: sala, name: Sala, rect: [0.0, 0.0, 5.0, 4.0]}   # [x, y, w, h]
      - id: lavabo
        name: Lavabo
        kind: wet
        polygon: [[8.5, 0.0], [10.0, 0.0], [10.0, 2.0], [8.5, 2.0]]

    walls:
      - [[0.0, 0.0], [10.0, 0.0]]          # [a, b] segment
      - {a: [10.0, 0.0], b: [10.0, 6.5], thickness: 0.2}

    openings:
      - {kind: door, at: [1.2, 6.5], width: 1.0, angle: 0}
      - {kind: window, at: [2.5, 0.0], width: 2.0, angle: 0}
      - {kind: door, at: [6.5, 3.0], width: 0.8, angle: 90, swing: -1}

    devices:
      - {entity_id: light.sala, at: [2.5, 0.8], label: Luz Sala}
      - {entity_id: climate.sala, at: [4.2, 0.6], kind: climate, room: sala}
```

Field notes:

- **Rooms** may use `rect: [x, y, w, h]` instead of `polygon:` — most real
  houses are mostly rectangles, and the shorthand keeps the file readable.
  A `polygon:` needs at least 3 points; an explicitly closed ring (first
  point repeated at the end) is tolerated and de-duplicated.
- **`kind:`** on a room selects a special tint in the renderer. Recognised
  values: `outdoor` / `garden` / `balcony` / `terrace` (outdoor tint),
  `service` / `garage` / `utility` / `storage` (service tint), `wet` /
  `bathroom` / `laundry` (wet-area tint). Anything else renders as a plain
  room.
- **Walls** are `[a, b]` point pairs or `{a:, b:, thickness:}` maps;
  `thickness` defaults to `0.15` (units).
- **Openings** (`kind: door | window | opening`) are drawn as a gap in the
  wall plus a swing arc for doors: `at` is the opening's centre, `width` its
  span, `angle` the direction (degrees) of the wall it sits in, `swing`
  `+1`/`-1` picks which side a door swings toward.
- **Devices** pin an entity to a point: `entity_id` (must contain a `.`),
  `at: [x, y]`, optional `kind:` (`light | switch | climate | sensor |
  cover | lock | camera | auto`; `auto` resolves from the entity_id's
  domain), `label:` (display name override) and `room:` (explicit room id,
  otherwise inferred from which room's polygon contains `at`).

Loading is strict about shapes and forgiving about ordering/optional
fields: a bad polygon, a device id with no domain, or a missing `floors`
key raises `PlanError` naming the floor/room so a bad file is easy to fix.

## SVG import

The SVG is the *source drawing* (Inkscape, Illustrator, QCAD export); the
YAML plan is the generated artefact the app actually reads. Import once,
commit the YAML, and hand-tune it afterward — re-importing is cheap, so
treat the YAML as disposable relative to the drawing.

Meaning is carried by each element's Inkscape "Label" (Object Properties
dialog), falling back to its `id`, colon-separated:

```
floor:ground:Térreo:0        a <g> layer holding one storey (id:name:level)
room:living:Sala:living      a closed shape -> Room(id, name, kind)
wall                         an open path/polyline -> wall segments
door / window                a short segment -> an Opening
device:light.sala:Luz Sala   a circle/small shape -> Device anchor
```

Unlabelled elements are ignored, so dimension lines, hatching and title
blocks can stay in the drawing. Supported shapes: `rect`, `circle`,
`ellipse`, `line`, `polygon`, `polyline`, `path` (Béziers are flattened by
sampling; SVG arcs are approximated by their chord). `transform` attributes
compose down the element tree.

```bash
python tools/svg2plan.py casa.svg --px-per-unit 100 --inspect
python tools/svg2plan.py casa.svg --px-per-unit 100 -o config/floorplan.yaml
```

`--px-per-unit` is SVG user units per plan unit (100 means 100px = 1m).
Run with `--inspect` first to see what the importer found — room areas,
device positions, counts per floor — before writing anything.

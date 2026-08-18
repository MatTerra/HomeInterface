# HomeInterface

A house control panel built with pygame, styled after Airbus ECAM / Embraer
EICAS avionics displays: dark ground, angular vector geometry (chamfered
corners, corner brackets, tick scales — no rounded rectangles), and colour
that is semantic rather than decorative. It reads Home Assistant entities
(or a built-in mock backend) and renders them onto a floor plan, an
overview "vitals" page and a raw entity register.

It was built around two hard requirements:

- **Resolution independence.** There are no bitmaps or pre-rendered assets
  anywhere in the UI. Every shape — strokes, chamfers, fonts, gauges — is
  generated from geometry at draw time, at the exact pixel size the current
  window demands. The same code has to look right on a 1280x800 tablet and
  an 8K wall panel, with no blur and no re-export step.
- **A shareable, multi-storey floor-plan config.** The house is described in
  a plain YAML file (or generated from an SVG drawing) that knows nothing
  about pygame or Home Assistant. Hand it to someone else and it renders
  identically against their own entity ids.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate      # Linux/macOS

pip install -r requirements.txt
python main.py
```

It ships with a mock backend (`backend.kind: mock` in `config/app.yaml`), so
it runs and drifts plausible sensor values with no Home Assistant instance
in reach — useful for development, demos and screenshots.

## CLI flags and controls

`python main.py` accepts (see `homeinterface/app.py:main`):

```
-c, --config PATH     path to app.yaml (default: config/app.yaml)
--plan PATH            override the floor plan file from the config
--backend {mock,homeassistant}
                        override backend.kind
--width INT
--height INT
--fullscreen
--density FLOAT        UI scale multiplier (touch panels: 1.2-1.5)
--driver {auto,window,fbdev}
                        output path: SDL window, or an mmap'd SPI framebuffer
--fbdev PATH           framebuffer device (default: probe /dev/fb1, /dev/fb0)
--touch SPEC           touch device: auto | none | /dev/input/eventN
--alternative          alternative small-screen shell (see below)
--custom [DASHBOARD]   custom shell: the screens declared in a dashboard file
                        (default: config/dashboard.yaml)
```

Global keyboard/mouse map:

| Key                | Action                              |
|---------------------|--------------------------------------|
| `ESC`               | Quit                                  |
| `TAB`                | Next screen                           |
| `1`-`9`              | Jump to screen N                      |
| `F11`                | Toggle fullscreen                     |
| `F12`                | Save a screenshot to `screenshots/`   |
| `F3`                 | Toggle the FPS/resolution readout     |
| `F5`                 | Reload the dashboard (`--custom` only)|

On the plan screen specifically:

| Input               | Action                              |
|---------------------|--------------------------------------|
| Mouse wheel          | Zoom                                  |
| Right-drag           | Pan                                   |
| `PGUP` / `PGDN`      | Change floor                          |
| `F`                  | Fit view (reset zoom/pan)             |
| `G`                  | Switch overview: drawing <-> room grid |
| Tap a device         | Select it (shows the inspector)       |
| Tap the same device again | Actuate it (toggle/etc.)         |
| Tap a room           | Show that room's devices              |
| Tap `GRID` / `PLAN`  | Same switch as `G`, for touch         |

The overview stage has two presentations. The drawing is true to scale, which
makes a lavatory or a corridor a small target; `GRID` replaces it with one
card per place - zones counted once, like the drawing names them - each an
equal touch target showing how many of its devices are on. Both lead into the
same focus stage, so the choice is only about finding the place.

## The alternative shell (`--alternative`)

`python main.py --alternative` (or `ui: {shell: alt}` in `app.yaml`)
starts a second, independent set of screens. Nothing of the stock interface
changes: the flag only picks `ALT_SCREEN_TYPES` instead of `SCREEN_TYPES` and
moves the nav from the side rail to a bottom bar.

Why it exists: on 480x320 the drawing is the bottleneck. A storey drawn to
scale gives a lavatory a target a couple of millimetres wide, the inspector
column ends up narrower than a slider, and the side rail plus the two-line
title bar eat about a fifth of the panel before any content is drawn.

What the alternative does instead:

* **No drawing, no gestures.** Places are cards, devices are rows. There is
  no pan, no zoom and no scrolling (this panel is resistive); what does not
  fit goes on a page you turn with two half-width buttons.
* **Drill-down, one stage at a time.** `places -> place -> device`, with a
  single `< BACK` button in a fixed spot. Each stage owns the whole content
  rectangle.
* **The common command never drills in.** Every place card carries a power
  chip that toggles the whole zone/room from the home screen.
* **Chrome shrinks.** Title bar 24 design units instead of 30, no side rail,
  and a full-width bottom tab bar - the easiest thing on a panel to hit
  without looking.
* **Rows grow into the spare space** rather than leaving a remainder at the
  bottom, so targets are as large as the page allows.
* **A grid when the width pays for it.** Device rows go up to three columns
  and place cards up to two (a card also carries a power chip and a count).
  The rule is measured both in design units and in real pixels, since design
  units shrink with the panel; a row whose state no longer fits beside its
  name stacks the two instead of truncating the name away.

Same functionality, same backend calls: floors, zones (with the ZONE/room
scope chips), group master toggle, group brightness/target, per-device
toggle and brightness/temperature/cover position, vitals, quick controls,
annunciators, and the unchanged systems register. `VITALS` splits into three
full-screen sections (`VITALS | QUICK | STATUS`) instead of three columns.

Screens live in `homeinterface/screens/alt.py`; `start_screen` and the `1`-`9`
shortcuts work as before, because the alternative screens reuse the same
`key`s (`plan`, `overview`, `systems`).

## The custom shell (`--custom`)

`python main.py --custom` (or `ui: {shell: custom}` in `app.yaml`) runs a
third shell whose screens are not written in Python at all: they are declared
in `config/dashboard.yaml`. The stock and alt shells are untouched.

### One tree, no screen list

A dashboard file is one `root:` node. Containers hold nodes, components draw.
A tab bar is an ordinary container - not chrome - so a dashboard that wants
one declares it, and a dashboard that does not, does not have one. Any node
may carry an `id:`, and `id` is what navigation targets. `start:` names the id
that opens first. The title bar and the alert footer stay chrome.

### The grid

A screen is **six units across and three down** (twelve by six cells
internally). Spans are written per child, Home Assistant style:

```yaml
- {type: toggle, entity: light.sala, columns: 6, rows: 1}
```

Rows may be asked for in halves; columns may not. Half a unit is 37.6 design
units tall, which clears a device row, but only 38.8 wide, which does not
clear the 40-unit touch minimum. Leave a span out and the container decides:
a `rows:` child stretches across, a `cols:` child takes an equal share, a
`grid:` child is one unit. See `docs/adr/0001`.

A container that holds more than fits gives up one of its own rows to a
pager (`< / >`, scrollbar-like) - that is `overflow: auto`, the default. Set
`overflow: clip` and it shows what fits plus a `+N MORE` count instead.

### Containers and components

| Containers | |
|---|---|
| `rows` | one child per line |
| `cols` | children side by side |
| `grid` | row-major flow |
| `chips` | `cols` with compact children |
| `tabs` | one pane at a time; `bar: top \| bottom \| none` |

| Components | |
|---|---|
| `floorplan` | the scale drawing; a tap reports its room |
| `places` | every zone and lone room as a card, power chip on each |
| `device-rows` | the devices a selector picks out, one row each |
| `toggle` `slider` `button` `tile` | controls |
| `readout` `arc-gauge` `bar-gauge` `lamp` `messages` `clock` `panel` | indicators |

### Data, without a template language

There is no Jinja and no expression language (`docs/adr/0002`). Four narrow
mechanisms cover the ground:

* **Binding** - `entity:` on a component. Its properties are what that
  component's placeholders read. `entity: {id: ..., precision: 1, unit: "°C"}`
  carries the formatting.
* **Placeholder** - `{state}`, `{name}`, `{attributes.brightness}` read the
  node's own entity; `{sensor.outdoor_temperature.state}` names another one
  outright. Lookups only: no arithmetic, no filters, no calls.
* **Repeat** - `from:` a selector (`room`/`zone`/`floor`/`kind`, AND-ed) plus
  a `template:` child, stamped once per match with `$entity` bound.
* **Condition** - `visible_if:` takes one predicate (`state`, `above`,
  `below`, `exists`). Nesting supplies AND; there is no `or`.
* **Level map** - `levels:` pairs predicates with theme roles, first match
  wins, the rule with no condition is the fallback and comes last. This is
  how "amber when the door is open" is said:

```yaml
- type: lamp
  entity: binary_sensor.porta_frente
  label: FRONT DOOR
  levels:
    - {state: "on", level: caution}
    - {level: normal}
```

### Actions

`on_press:` takes `toggle`, `back`, `none`, `{goto: <id>, params: {...}}` or
`{call: light.turn_off, data: {...}}`. Params are `$name` values that the
target pane's selectors and placeholders can then read.

### When it goes wrong

Structure is validated at load: an unknown `type:`, a bad span, a dangling
`goto` or a duplicate `id` refuses to boot, naming the file and line. A
missing entity at runtime draws as `inop` rather than crashing.

Reload without restarting: **SIGHUP** (`kill -HUP <pid>`) on the panel, `F5`
in the dev window. A dashboard that fails to reload leaves the running one up
and reports the error in the message strip - the panel never goes black.

## Running on the SPI panel

The target hardware is a Raspberry Pi 3 B driving an Adafruit PiTFT 3.5"
(480x320, HX8357D + STMPE610 touch). That panel is not HDMI: the kernel's
`fbtft` driver exposes it as `/dev/fb1`, and SDL2 has no fbdev video driver,
so there is nothing for pygame to open. `homeinterface/fbdev.py` closes the
gap without X, Wayland or an `fbcp` mirroring daemon:

- it draws into an **RGB565** surface — the panel's own pixel format, so
  presenting a frame is a copy with no conversion pass;
- it writes **only the rows that changed** into the mmap'd device, because
  fbtft repaints over SPI exactly what was dirtied and a full 480x320x16bpp
  frame is 307kB (~77ms at 32MHz, i.e. 13fps if you repaint everything);
- it reads the touchscreen straight from `/dev/input/event*` and posts pygame
  mouse events, so widget code needs no touch-specific branch.

```bash
python main.py --driver fbdev        # force the panel
python tools/touchcal.py            # tap four targets, get a calibration block
```

Full walkthrough — OS image, `config.txt` overlay, groups, calibration,
systemd unit, tuning and troubleshooting — in
[`docs/raspberry-pi.md`](docs/raspberry-pi.md).

## How scaling works

`homeinterface/scaling.py` splits layout into two deliberately different
mechanisms:

- **`Viewport.u(v)`** — a uniform *design-unit* scale, used for "ink": stroke
  widths, chamfer cuts, gaps, padding and font sizes. Design units are
  expressed against a 1920x1080 reference canvas; `Viewport.scale` is
  `min(width/1920, height/1080) * density`, so the same design-unit value
  always turns into a proportionally similar number of pixels regardless of
  panel size.
- **`Box`** — fractional sub-rectangles of whatever real rectangle the shell
  hands a screen (`box.cols(0.74, 0.26)`, `box.rows(0.66, 0.34)`, `.grid()`,
  `.pad()`, `.fit()`, …). Layout is **not** letterboxed to a fixed
  1920x1080 canvas: on a 21:9 wall panel a letterboxed 16:9 canvas would
  waste a third of the screen doing nothing. Position and size are always
  fractions of the real surface; only the ink scales uniformly on top of
  that.

In practice this means widgets are positioned with `Box` and drawn with
`ctx.u()` / `ctx.px()` / `ctx.font_px()` — never with a raw pixel constant.

Set `display.density` in `config/app.yaml` (or pass `--density`) to bump
every design-unit value up on a small touch panel, typically `1.2`-`1.5`.

## Floor plan format

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

## Home Assistant

Set `backend.kind: homeassistant` in `config/app.yaml`, then get a
long-lived access token from your Home Assistant profile page → Security →
Long-lived access tokens. Prefer environment variables over putting the
token in the config file:

```bash
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN=<long-lived access token>
```

`HA_URL`/`HA_TOKEN` are read first and win over `backend.url` /
`backend.token` in the YAML, so the committed config never has to carry a
secret. Optionally scope which entities are pulled in with
`backend.entity_filter: [light., switch., sensor., climate.,
binary_sensor., cover.]` — an allow-list of entity_id prefixes, useful to
keep a large installation light.

The backend opens one WebSocket connection that carries the initial
`get_states` dump, the `state_changed` event subscription, and outgoing
`call_service` commands. It reconnects automatically with exponential
backoff (`RECONNECT_MIN=2s` up to `RECONNECT_MAX=30s`, `*1.8` per attempt)
and surfaces link loss as a warning-level alert on the footer strip.

## Colour semantics

Colour in an avionics display is semantic, never decorative — keep these
meanings when adding a widget; that discipline is what makes the panel read
as an aircraft system rather than a Star Trek prop.

| Colour  | Meaning                                          |
|---------|---------------------------------------------------|
| Green   | system normal, powered, in use                    |
| White   | titles, labels, selected-but-neutral               |
| Cyan    | units, setpoints, data the operator may change     |
| Amber   | caution — abnormal but not immediately dangerous   |
| Red     | warning — requires immediate action                |
| Magenta | targets and constraints (setpoint markers on gauges) |
| Grey    | inoperative, unpowered, unavailable                |

Every colour can be overridden as `#RRGGBB` under `theme:` in
`config/app.yaml`. New widgets must map their state to one of these seven
meanings via `Theme.status_color()` — not invent a new colour for "looks
nice here".

## Architecture

```
homeinterface/
  app.py               output, chrome (title/rail/footer), main loop, argparse entry
  config.py             load_config(): YAML merged over DEFAULTS
  fbdev.py               SPI panel: RGB565 row-diffing framebuffer, evdev touch
  scaling.py             Viewport (design units -> px) and Box (fractional layout)
  theme.py                Theme dataclass, colour parsing, status_color()
  draw.py                  vector primitives: chamfered rects, hairline grids, ticks
  fonts.py                  FontBook: resolves/rasterises/caches text at exact px size
  floorplan/
    model.py               FloorPlan/Floor/Room/Device/Opening/Wall, polygon math
    loader.py                YAML <-> model, PlanError, rect: shorthand
    svg_import.py             Inkscape-label-driven SVG -> FloorPlan
    renderer.py               FloorRenderer: draws a Floor into a rect, PlanView
  backend/
    base.py                 Backend ABC, Entity/Link/Alert, thread-safe snapshot store
    mock.py                   MockBackend: drifting sensors, instant commands
    homeassistant.py          WebSocket backend, auto-reconnect
  ui/
    base.py                  Widget/Pressable/WidgetGroup, UIContext
    controls.py                Button, Slider, ToggleButton, TabStrip
    indicators.py               Panel, ArcGauge, BarGauge, Readout, StatusLamp, EntityTile
  dashboard/
    schema.py                Node/Span/Binding/Predicate/Repeat/Action + validation
    loader.py                  dashboard.yaml -> node tree, errors with file:line
    registry.py                 the type: table; built-ins go through it too
    components.py                one builder per type:, plus places/device-rows/floorplan
    build.py                      layout engine + DashboardScreen (the custom shell)
  screens/
    base.py                  Screen contract (layout/draw/handle/lifecycle)
    plan.py                    floor plan + device inspector (primary page)
    alt.py                      --alternative shell: drill-down places/devices
    overview.py                 ECAM-style vitals: gauges, quick controls, annunciators
    systems.py                   entity register + link diagnostics
```

The key seam: **the UI never talks to Home Assistant directly.** A
`Backend` runs its own I/O on a background thread (a WebSocket event loop
for `HomeAssistantBackend`, a drift simulation thread for `MockBackend`)
and publishes immutable `Entity` snapshots behind a lock. Screens only ever
call `backend.get()` / `backend.snapshot()` / `backend.by_domain()` to
read, and `backend.call()` / `.toggle()` / `.set_brightness()` etc. to
write (fire-and-forget; failures surface as alerts). The render loop reads
snapshots and never blocks on the network.

## Adding a new screen

1. Subclass `Screen` (`homeinterface/screens/base.py`) in a new module under
   `homeinterface/screens/`.
2. Set class attributes `key` (stable id, also the `1`-`9` shortcut order),
   `title` (nav rail label) and `subtitle` (title bar caption).
3. Implement `layout(rect, ctx)` to lay out widgets into `rect` using `Box`,
   and `draw(surface, ctx)` / `handle(event, ctx)` if you need more than the
   default widget-group forwarding.
4. Register the class in `SCREEN_TYPES` in `homeinterface/app.py`.

## Adding a new widget

1. Subclass `Widget` (read-only instrument) or `Pressable` (tap/click
   target) from `homeinterface/ui/base.py`.
2. Implement `layout(rect)` to store the rect and `draw(surface, ctx)` to
   render — use `homeinterface/draw.py` primitives (`chamfer_rect`,
   `hairline_grid`, etc.) and `fonts.blit_text`, never blit a bitmap.
3. Map any state to colour through `ctx.theme.status_color(level)`, not a
   hardcoded RGB tuple.
4. Size everything through `ctx.u()` / `ctx.px()` / `ctx.font_px()` so the
   widget scales with the panel like everything else.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Tests are headless (`SDL_VIDEODRIVER=dummy`, set once in
`tests/conftest.py`) and cover scaling math, theme parsing, the floor plan
model/loader/SVG importer, the mock backend and `websocket_url()`, config
merging, and an end-to-end smoke render of every screen at several panel
sizes.

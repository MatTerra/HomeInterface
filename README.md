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
--alternative          alternative small-screen shell (see Shells)
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

## Shells

A shell is a whole set of screens plus the navigation between them; one is
picked at startup and they do not mix.

- **stock** (default) - the floor plan drawn to scale, a vitals overview and
  the entity register, navigated from a side rail.
- **alt** (`--alternative`) - drill-down navigator for small panels: places ->
  place -> device, one thing on screen at a time, a bottom tab bar, and paging
  instead of scrolling.
- **dashboard** (`--custom`) - screens declared in a YAML file rather than
  written in code. This is where the project is heading; see
  [`docs/dashboard.md`](docs/dashboard.md).

Stock and alt predate the dashboard shell and are on their way out - they are
maintained, not extended. Details in [`docs/shells.md`](docs/shells.md).

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

## Floor plan

The house is one YAML document (`config/floorplan.yaml`), or an SVG drawing
converted by `tools/svg2plan.py`. It knows nothing about pygame or Home
Assistant, so the same file renders identically against someone else's entity
ids. Format and import: [`docs/floorplan.md`](docs/floorplan.md).

## Where things are

- [`CLAUDE.md`](CLAUDE.md) - the file map, and the invariants anyone editing
  this code has to hold (input, colour, scaling, the backend seam).
- [`CONTEXT.md`](CONTEXT.md) - the glossary: floor, room, zone, place, device,
  entity, and the dashboard vocabulary. Read it before naming something new.
- [`docs/adr/`](docs/adr) - decisions that are expensive to reverse, and why.
- [`docs/floorplan.md`](docs/floorplan.md) - the plan YAML and the SVG import.
- [`docs/dashboard.md`](docs/dashboard.md) - the dashboard format.
- [`docs/architecture.md`](docs/architecture.md) - scaling and colour, long form.
- [`docs/raspberry-pi.md`](docs/raspberry-pi.md) - building the panel, start to
  finish.
- [`docs/backlog.md`](docs/backlog.md) - known gaps.

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

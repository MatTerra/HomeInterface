# Architecture notes

The file map and the invariants an editor has to hold live in
[`CLAUDE.md`](../CLAUDE.md). This page is the long form of two of them.

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

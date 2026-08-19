# 6. Floorplan focus stage and in-place selection: the `set` action

Date: 2026-08-19

## Status

Accepted

## Context

The stock plan screen (`screens/plan.py`) is two stages: an overview of the
whole storey, and a focus stage where one room or zone fills the rectangle
with its devices and a live inspector panel beside it. Tapping a device does
not leave the screen - the plan stays visible and the inspector beside it
updates in place. This is the piece of stock parity most worth keeping: the
whole reason the inspector sits next to the drawing rather than behind it.

The dashboard's nav model (ADR 0003) only writes params at pane entry: a
`goto` action sets `$name` values once, and a pane's selectors and
placeholders read them for as long as that pane is on screen. Nothing lets a
tap update a param without navigating. Reusing `goto` for a device tap would
work grammatically, but it replaces the whole pane - the floorplan itself
would leave the screen on every tap, which is exactly the regression from
stock parity that matters here.

## Decision

`floorplan` stays one component (not a second type for the focus stage). It
gains, as intrinsic behavior rather than YAML-configurable properties:

- Zoom (wheel + in/out/reset buttons) and pan (right-drag), fixed constants
  mirroring stock. The buttons are the tap-reachable path required by ADR
  0004; wheel and right-drag exist only as the desktop shortcuts that ADR
  already allows.
- A `focus:` prop, bound to `$room` or `$zone`. When set, the component
  restricts drawing to that room/zone, fills the rect, grows its markers, and
  turns on area labels - matching stock's `self.focused` behavior. Overview
  to focus is an ordinary `goto` with `room=`/`zone=` params; the component
  reads its own `focus:` prop rather than tracking stage internally.

One new action kind, **`set`**: `{kind: set, param: <name>}`, given a value
from the tap that fired it (the entity id under a device marker, for
`floorplan`). It writes one param into the *current* pane's scope without
navigating. `floorplan` uses it for device selection inside focus mode.

Two new components read that param: **`device-inspector`** (bound to the
`$device` param the tap set: toggle + a kind-specific slider) and
**`zone-inspector`** (bound to `$zone`/`$room`: master toggle, group sliders
via ADR 0005's group-aware `slider`, and the ZONE/ROOM scope switch). They
sit beside `floorplan` in the tree, gated by `visible_if` on whether the
param is set - mirroring stock's own `_build_inspector` /
`_build_zone_inspector` split.

## Consequences

Nav-scope params are no longer read-only for the lifetime of a pane: `set`
mutates the current pane's param, where until now only `goto` (entering a new
pane) could establish one. `visible_if` and bindings treat an unset param the
same way they already treat a missing one - as empty - so no new "unset"
state was invented.

`set` is deliberately narrow: it copies one value into one name. It is not a
general local-variable mechanism, does no arithmetic, and cannot compute a
value from another param - ADR 0002 still holds. If a future need wants more
than that, it is a new decision, not a quiet widening of this one.

Zoom, pan and focus remain properties of `floorplan` specifically; no other
component gains pan/zoom by this decision. A dashboard that wants stock-style
plan parity reaches for `floorplan` + `device-inspector` + `zone-inspector`
together, the same three-part shape stock's own code has.

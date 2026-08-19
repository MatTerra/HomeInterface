# Backlog

Known gaps, recorded so they are not rediscovered. Not a roadmap: an entry
here is a thing that is wrong or missing today, with enough context to decide
whether it is worth fixing.

## Input reachable only from a desktop

**Plan zoom and pan are unreachable on the panel.** `screens/plan.py` zooms on
`MOUSEWHEEL` and pans on mouse buttons 2/3. The resistive panel produces
neither (see `docs/adr/0004-no-gestures-on-resistive-touch.md`), so on the
device the drawing is fixed at its initial framing.

*Fix, if the stock shell survives:* `+` / `-` / `reset` buttons and
single-finger drag to pan. Held back because the stock shell is on its way out
- see below.

The on-screen `WHEEL ZOOM` / `RIGHT-DRAG PAN` hints are *not* part of this:
they are drawn only above 700 design units of width, so the panel never sees
them and a dev window - where both inputs work - does.

## Shells

**Stock and alt shells are both on their way out.** The dashboard shell
(`homeinterface/dashboard/`) is the target: screens declared in YAML rather
than written in code. Stock (`plan.py` / `overview.py` / `systems.py` plus the
side rail) and alt (`screens/alt.py`) predate it.

Nothing is removed until the dashboard shell covers what they do - the scale
drawing being the hard part. Until then they are maintained, not extended: new
work goes into components and the dashboard schema.

## Home Assistant mapping fails silently

`Room.ha_label` maps a room to a Home Assistant **label** by name, and
`Zone.ha_area` maps a zone to an **area** by name. If either name drifts on
the Home Assistant side - a rename, an accent, a stray space - the lookup
finds nothing and the place simply never lights up, commands nothing, and
reports no error.

This is the most likely failure of the whole system in normal use, and it is
currently invisible. It should surface: an alert naming the plan-side term
that matched nothing, raised once at registry sync rather than per frame.

## Documentation still owed

`docs/dashboard.md` - the mental model of the dashboard format (node tree,
6x3 author units, bindings, repeat, selectors, conditions, level maps) plus
one commented example dashboard. Deliberately deferred until the schema stops
moving; `dashboard/schema.py` remains the authoritative definition, and
`docs/adr/000{1,2,3}` carry the reasoning.

README split - the floor plan YAML format and the SVG import move out to
`docs/floorplan.md`, leaving the README as what-it-is, quick start, flags and
links. The file map lives in `CLAUDE.md` and is not duplicated back.

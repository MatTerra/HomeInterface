# The hand-built shells: stock and alt

Both predate the dashboard shell and are on their way out - maintained, not
extended (see [`backlog.md`](backlog.md)). The stock shell is the original
set: the floor plan drawn to scale, the vitals overview and the entity
register, navigated from a side rail.

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

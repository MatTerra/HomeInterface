# 4. No gestures on the resistive panel: paginate, never scroll

Date: 2026-08-18

## Status

Accepted

## Context

The panel is an Adafruit PiTFT 3.5" - a **resistive** touchscreen read through
evdev. `fbdev.py` translates that stream into pygame events, and it can only
ever emit three of them: `MOUSEBUTTONDOWN` when a finger lands, `MOUSEMOTION`
while it drags, `MOUSEBUTTONUP` when it lifts.

Four input paths that exist on a desktop therefore do not exist on the panel:

- `MOUSEWHEEL` - there is no wheel, and a resistive digitiser reports one
  contact, so there is no two-finger scroll to synthesise it from.
- Buttons 2 and 3 - one contact, one button.
- Hover - motion is only reported while a finger is pressed, so any state
  keyed on `ctx.pointer` without a press is dead on the panel.
- Pinch and other multi-touch gestures - single contact.

This was found the expensive way. `systems.py` shipped as "a scrollable list"
and could not be scrolled on the hardware, and the stock plan's zoom and pan
are still wheel- and right-button-only. The code was correct on the dev window
and inert on the device, with nothing failing loudly to say so.

Long-press is technically available (down, wait, up), but a resistive panel
jitters under a held finger and gives no tactile feedback, so it fires by
accident.

## Decision

Every function must be reachable with single touch: **a tap, or a
press-drag-release inside one widget**.

- Wheel, buttons 2/3, hover and pinch may exist as **desktop development
  shortcuts**, never as the only path to a function.
- Long-press is allowed only as a last resort, when no tap-reachable design
  works.
- Content that does not fit is **paginated** - a prev/next pair the finger can
  hit - never scrolled. `systems.py` and the alt shell already work this way.
- The panel is never told about an input it does not have: no on-screen hint
  naming a wheel, a right-click or a gesture.

## Consequences

Layouts must know their own capacity, which is why a container that overflows
gives up one of its rows to a pager (see ADR 0001 for the unit grid this is
measured in). Lists cannot be arbitrarily long and cheap; a page has to be a
designed quantity.

Reviews of any new screen ask one question first: can a finger reach all of
this? Tests can assert it, since a test that only ever posts
`MOUSEBUTTONDOWN`/`UP` exercises exactly what the hardware can produce.

The stock shell's plan zoom and pan remain wheel- and right-button-only, so
they are unreachable on the panel. That is recorded in `docs/backlog.md`
rather than fixed here, because the stock shell is on its way out.

# 5. Shell parity: new components and repeat sources, no new grammar family

Date: 2026-08-19

## Status

Accepted

## Context

Porting the stock and alt shells to dashboard files (a separate PR from the
custom shell itself, ADR 0003) surfaced concrete vocabulary gaps: things the
hand-built screens do that no `type:` or `repeat.over:` value can express yet.
None of them need arithmetic, filters or templating (ADR 0002 still holds);
they are missing nouns, not a missing expression language.

## Decision

Add, as plain extensions of the existing registry/schema:

- **`label`** component - a non-interactive caption. `text:` for a literal
  string, optional `binding:`/`format:` for a computed one (e.g. "N PLACES").
- **Group-aware `toggle`/`slider`** - both accept the same `entities:`/
  selector shape `power-chip` already takes. Given more than one entity they
  call the backend's group operations (`toggle_group`,
  `set_group_brightness`, `set_group_temperature`) instead of the single-
  entity ones.
- **`repeat.over: floors`** and **`repeat.over: rooms`** alongside the
  existing `devices`/`places`.
- **`repeat.over: entities`**, two shapes:
  - a literal id list, for a fixed set an author names once (quick controls).
    Reuse across nodes is a plain YAML anchor/alias, not new grammar.
  - a live `domain:` selector, read off the backend's own snapshot rather
    than the plan - for "every entity of this domain the backend exposes,"
    which the plan-based selector cannot answer because it only knows
    entities placed as devices on some floor.
- **`attr-list`** component - bound to one entity, prints whichever of a
  fixed key whitelist (`brightness`, `current_temperature`, `temperature`,
  `current_position`) exist on it. This needed a dedicated component because
  the line count is dynamic per entity; no `{attributes.x}` placeholder
  sequence can print "only the keys present."
- **`link-status`** component - draws backend-level connection diagnostics
  (link state, backend type, entity/alert counts, revision, last error).
  None of that is one entity's state or attributes, so no binding could reach
  it; a single-purpose component was preferred over widening the placeholder
  grammar to a non-entity data source.
- **`tabs.bar: left | right`** alongside the existing `top | bottom | none`,
  for a side-rail nav.

## Consequences

`schema.py`/`loader.py` stay pygame-free; the new pieces above are grammar
(schema/loader) plus registry entries (`components.py`), same split as every
existing component.

`attr-list` and `link-status` are deliberately narrow: one job each, not a
general "print arbitrary backend facts" mechanism. Richness lives in the
component, not in a wider grammar - consistent with ADR 0002.

The live `domain:` selector on `repeat.over: entities` is the first repeat
source that reads the backend instead of the plan. It is additive: existing
`over:` values are unaffected, and a plan-based `Selector` (`room`/`zone`/
`floor`/`kind`) is still what `devices`/`places`/`floors`/`rooms` use.

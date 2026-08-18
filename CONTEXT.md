# Context

Glossary for HomeInterface. Terms only — no implementation detail, no decisions.
Architectural decisions live in `docs/adr/`.

## Spatial model

**Floor** — one level of the house (térreo, superior). Owns rooms, walls,
openings and devices. Floors share one coordinate system so they stay aligned
when flipped between.

**Room** — one enclosure on a floor, with a polygon. Any enclosure, not only a
bedroom: sala, garagem and varanda are all rooms.

**Zone** — a named grouping of rooms, possibly spanning floors. A zone owns no
geometry of its own; its members keep their polygons, areas and devices.

**Place** — one unit of control on the drill-down shell: either a zone, or a
room that belongs to no zone. The set of places is what the operator picks
from before commanding anything.

**Device** — an entity positioned on the plan, belonging to a room.

## Presentation

**Shell** — one whole set of screens plus the navigation between them. Shells
are selected at startup and do not mix.

**Stock shell** — the original screens: the scale drawing plus the side rail.

**Alt shell** — the drill-down navigator for small panels: places → place →
device, one thing on screen at a time, no panning or scrolling.

**Custom shell** — a shell whose screens are declared in a dashboard file
rather than written in code.

**Dashboard file** — the YAML document describing a custom shell.

**Places grid** — the screen listing every place as a card.

## Dashboard layout

**Cell** — the smallest division of a screen's layout grid. A screen is twelve
cells across and six down.

**Unit** — the step an author places things on: two cells square. A screen is
therefore six units across and three down, and a half unit is the finest span
an author can ask for.

**Span** — how many units a component occupies, across and down.

**Pager** — the row of prev/next buttons a container gives up one of its own
rows to, when it holds more than it can show. Only a container at least two
rows tall can carry one.

## Dashboard vocabulary

**Node** — one entry in a dashboard's tree. Either a container, which holds
other nodes, or a component, which draws something.

**Component** — a node that draws. It may be bound to one entity.

**Binding** — the entity a component is attached to. Its properties are what
the component's placeholders read.

**Placeholder** — a lookup written into a text field. It reads a property of
its component's own binding, or names another entity outright when the
component has no binding of its own to read. It looks nothing up but
properties: no arithmetic, no filters, no calls.

**Repeat** — generating one child per match, from a selector over the plan and
a child shape to stamp out.

**Selector** — a description of which places or entities something applies to,
by room, zone, floor or kind, rather than by listing entity ids.

**Condition** — a single predicate deciding whether a node is shown.

**Level map** — an ordered list of predicates paired with theme roles, first
match winning, deciding what colour a component draws in.

**Level** — the theme role a component draws in: normal, caution, warning,
data or target.

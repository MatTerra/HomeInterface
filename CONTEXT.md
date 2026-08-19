# Context

Glossary for HomeInterface. Terms only — no implementation detail, no decisions.
Architectural decisions live in `docs/adr/`.

## Spatial model

**Floor plan** — the whole drawing of one house: every floor, and everything
on them. It is shared as a document and is deliberately ignorant of both the
renderer and Home Assistant, so the same drawing renders identically against
someone else's entities.

**Floor** — one level of the house (térreo, superior). Owns rooms, walls,
openings and devices. Floors share one coordinate system so they stay aligned
when flipped between.

**Room** — one enclosure on a floor, with a polygon. Any enclosure, not only a
bedroom: sala, garagem and varanda are all rooms. A room always has geometry:
a place with no shape is not a room. An outdoor area is a floor of its own,
and "the whole house" is not a room at all.

**Zone** — a named grouping of rooms, possibly spanning floors. A zone owns no
geometry of its own; its members keep their polygons, areas and devices.

> Not Home Assistant's *zone*. There, a zone is a geographic region used for
> presence ("home", "work"); it has nothing to do with this one. See
> **Home Assistant vocabulary** below for what our terms map onto.

**Place** — one unit of control on the drill-down shell: either a zone, or a
room that belongs to no zone. The set of places is what the operator picks
from before commanding anything.

**Device** — a pin on the drawing: a position in a room, naming an entity.
A device is drawing, not state - it says *where* something is, never what it
is doing. What it is doing belongs to the **entity** it names.

## Home Assistant vocabulary

**Entity** — one controllable or observable thing as the backend reports it:
live state, read as an immutable snapshot. Two things share the name of an
entity: the **device** that pins it to a spot on the drawing, and the entity
itself. They are never the same thing.

**Area** (Home Assistant) — what a **zone** maps onto, by name.

**Label** (Home Assistant) — what a **room** maps onto, by name. Rooms are
labels and zones are areas, not the other way around.

**Link** — the state of the connection to the backend: offline, connecting,
online or degraded. It says whether what is on screen can be trusted, and
nothing about the house.

**Alert** — one line on the message strip: something the operator is being
told. Link and alert are separate channels. A link state is not an alert and
does not become one; an alert is not evidence about the connection.

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

**Focus** — the floor plan's second stage: one room or zone drawn alone and
filling the rectangle, its devices and inspector reachable, in place of the
whole storey the overview stage draws.

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

**Repeat** — generating one child per match, from a selector and a child shape
to stamp out. Most selectors describe a slice of the plan (by room, zone,
floor or kind); one instead describes a slice of the backend's own entity
list, by domain, for entities the plan does not place anywhere.

**Selector** — a description of which places or entities something applies to,
by room, zone, floor or kind, rather than by listing entity ids.

**Param** — a named value carried by a pane, set when a `goto` navigates to
it. A component's selectors and placeholders may read it. Most params are set
once, at entry, and hold for the pane's lifetime; a tap can also update one in
place, so a sibling component beside the one that changed it re-reads live.

**Condition** — a single predicate deciding whether a node is shown.

**Level map** — an ordered list of predicates paired with theme roles, first
match winning, deciding what colour a component draws in.

**Level** — the theme role a component draws in: normal, caution, warning,
data or target.

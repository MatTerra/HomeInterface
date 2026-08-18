# 1. A six-by-three author grid over a twelve-by-six internal one

Date: 2026-08-18

## Status

Accepted

## Context

A custom shell's screens are declared in a dashboard file, so every layout in
every dashboard has to be expressed in one coordinate vocabulary. That
vocabulary is permanent: once dashboards exist, changing it rewrites all of
them.

The reference panel is 480x320. After chrome the content rectangle measures
466 x 226 design units. The hand-built shells put real constraints on that
space: a labelled column needs 150 units of width, a device row needs 33.6
units of height, and no touch target may be under 40 units on either axis.

A fine grid (12 x 6) divides by 2, 3, 4 and 6 - exactly the column counts the
existing layout code picks - but its cell is 38.8 x 37.6 units, which is
under the touch floor on the horizontal axis. Authors writing spans in a unit
they can never use for one control invites layouts that validate and then
cannot be operated.

## Decision

The grid is twelve cells across and six down internally. Authors never see
cells: they place things in **units** of two cells square - a six-by-three
grid - and may ask for half a unit.

Half spans are accepted on rows and refused on columns. A half unit is 37.6
units tall, which clears the 33.6-unit device row that motivated the
precision; a half unit is 38.8 units wide, which does not clear the 40-unit
touch minimum.

## Consequences

Device rows fit six per screen where the hand-built shell fits five.

Place cards land at one unit tall - 75.2 units, against roughly 56 today - so
three card rows fit a page where four did. Cards get taller, not more
numerous.

Rows no longer grow into leftover space the way the hand-built shell does.
Spare space belongs to the grid, so a part-full page shows a gap at the
bottom rather than fatter rows.

A labelled control cannot be narrower than two units. One unit (77.6) is
enough for a chip, not for a name and a state.

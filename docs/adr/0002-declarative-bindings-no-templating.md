# 2. Declarative bindings, not a template language

Date: 2026-08-18

## Status

Accepted

## Context

Dashboards must show live values ("this room's temperature"), generate
children from the plan ("every light in this zone"), hide what is not there
("no climate device, no climate slider"), and colour by state ("red when the
door is open").

Home Assistant solves all four with Jinja in string fields. That is the
familiar answer, and familiarity with Home Assistant is a stated goal.

The panel is a Raspberry Pi driving an SPI display at 30fps. Jinja means an
expression evaluator, a sandbox around it, and a re-render trigger per
template string.

## Decision

No expression language. Four narrow mechanisms instead, each covering one of
the four needs:

- **Binding** - a component optionally carries an `entity`. Placeholders in
  its text read that entity's properties.
- **Repeat** - `from:` on a container generates one child per match against
  the plan, shaped by a `template:` child.
- **Condition** - `visible_if:` takes a single predicate per node. Nesting
  supplies conjunction; there is no `or`.
- **Level map** - `levels:` maps predicates to theme roles, first match wins.
  Colour is data, not code.

Placeholders resolve by lookup only: no filters, no arithmetic, no function
calls. Formatting belongs to the binding, which carries precision and unit.

## Consequences

A dashboard cannot compute. Anything needing arithmetic needs either a Home
Assistant template sensor upstream or a component in Python.

The predicate grammar is shared by `visible_if` and `levels`, so there is one
thing to learn and one thing to validate.

Every mechanism is inspectable without running it, which is what keeps
validation and the layout tests free of a display.

If this proves too tight, the escape hatch is a component in Python, not a
wider expression language.

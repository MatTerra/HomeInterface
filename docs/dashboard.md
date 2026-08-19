# The dashboard (custom shell)

Screens declared in YAML instead of written in code. This is the shell the
project is heading for; the hand-built ones are described in
[`shells.md`](shells.md).

**The schema is under active development.** `homeinterface/dashboard/schema.py`
is the authoritative definition and will be ahead of this page; the reasoning
behind the format is in [`adr/`](adr/) (0001 the author grid, 0002 bindings
without a template language, 0003 one node tree).

`python main.py --custom` (or `ui: {shell: custom}` in `app.yaml`) runs a
third shell whose screens are not written in Python at all: they are declared
in `config/dashboard.yaml`. The stock and alt shells are untouched.

### One tree, no screen list

A dashboard file is one `root:` node. Containers hold nodes, components draw.
A tab bar is an ordinary container - not chrome - so a dashboard that wants
one declares it, and a dashboard that does not, does not have one. Any node
may carry an `id:`, and `id` is what navigation targets. `start:` names the id
that opens first. The title bar and the alert footer stay chrome.

### The grid

A screen is **six units across and three down** (twelve by six cells
internally). Spans are written per child, Home Assistant style:

```yaml
- {type: toggle, entity: light.sala, columns: 6, rows: 1}
```

Rows may be asked for in halves; columns may not. Half a unit is 37.6 design
units tall, which clears a device row, but only 38.8 wide, which does not
clear the 40-unit touch minimum. Leave a span out and the container decides:
a `rows:` child stretches across, a `cols:` child takes an equal share, a
`grid:` child is one unit. See `docs/adr/0001`.

A container that holds more than fits gives up one of its own rows to a
pager (`< / >`, scrollbar-like) - that is `overflow: auto`, the default. Set
`overflow: clip` and it shows what fits plus a `+N MORE` count instead.

### Containers and components

| Containers | |
|---|---|
| `rows` | one child per line |
| `cols` | children side by side |
| `grid` | row-major flow |
| `chips` | `cols` with compact children |
| `tabs` | one pane at a time; `bar: top \| bottom \| none` |

| Components | |
|---|---|
| `floorplan` | the scale drawing; a tap reports its room |
| `places` | every zone and lone room as a card, power chip on each |
| `device-rows` | the devices a selector picks out, one row each |
| `toggle` `slider` `button` `tile` | controls |
| `readout` `arc-gauge` `bar-gauge` `lamp` `messages` `clock` `panel` | indicators |

### Data, without a template language

There is no Jinja and no expression language (`docs/adr/0002`). Four narrow
mechanisms cover the ground:

* **Binding** - `entity:` on a component. Its properties are what that
  component's placeholders read. `entity: {id: ..., precision: 1, unit: "°C"}`
  carries the formatting.
* **Placeholder** - `{state}`, `{name}`, `{attributes.brightness}` read the
  node's own entity; `{sensor.outdoor_temperature.state}` names another one
  outright. Lookups only: no arithmetic, no filters, no calls.
* **Repeat** - `from:` a selector (`room`/`zone`/`floor`/`kind`, AND-ed) plus
  a `template:` child, stamped once per match with `$entity` bound.
* **Condition** - `visible_if:` takes one predicate (`state`, `above`,
  `below`, `exists`). Nesting supplies AND; there is no `or`.
* **Level map** - `levels:` pairs predicates with theme roles, first match
  wins, the rule with no condition is the fallback and comes last. This is
  how "amber when the door is open" is said:

```yaml
- type: lamp
  entity: binary_sensor.porta_frente
  label: FRONT DOOR
  levels:
    - {state: "on", level: caution}
    - {level: normal}
```

### Actions

`on_press:` takes `toggle`, `back`, `none`, `{goto: <id>, params: {...}}` or
`{call: light.turn_off, data: {...}}`. Params are `$name` values that the
target pane's selectors and placeholders can then read.

### When it goes wrong

Structure is validated at load: an unknown `type:`, a bad span, a dangling
`goto` or a duplicate `id` refuses to boot, naming the file and line. A
missing entity at runtime draws as `inop` rather than crashing.

Reload without restarting: **SIGHUP** (`kill -HUP <pid>`) on the panel, `F5`
in the dev window. A dashboard that fails to reload leaves the running one up
and reports the error in the message strip - the panel never goes black.

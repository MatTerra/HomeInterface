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
| `tabs` | one pane at a time; `bar: top \| bottom \| left \| right \| none` |

| Components | |
|---|---|
| `floorplan` | the scale drawing; zoom/pan and a `focus:` stage, see below |
| `places` | every zone and lone room as a card, power chip on each |
| `device-rows` | the devices a selector picks out, one row each |
| `device-inspector` | one entity: a toggle plus a kind-specific slider |
| `zone-inspector` | a zone's (or room's) master toggle plus group sliders |
| `toggle` `slider` `button` `tile` | controls |
| `readout` `arc-gauge` `bar-gauge` `lamp` `messages` `clock` `panel` `label` | indicators |
| `attr-list` | one entity's attributes, whichever of a fixed key list exist |
| `link-status` | backend connection diagnostics - link state, counts, revision |

### `floorplan`: focus, and selecting a device (`docs/adr/0006`)

`floorplan` has two stages. With no `focus:` it draws the whole floor and a
tap on a room fires `on_press:` (usually a `goto` into a focus pane, with
`room:`/`zone:` params). With `focus: $room` or `focus: $zone` it fills the
rect with just that room (or that zone's rooms), grows the markers, and a tap
on a device fires a *different* action, `on_select:` - kept apart from
`on_press:` because the two mean different things at the same moment: one
navigates to a new pane, the other selects in place. Zoom (wheel, or the
+/-/FIT buttons) and pan (right-drag) work in both stages.

`on_select:` takes the same shapes as `on_press:`, plus one new kind, `set`:
`{set: <param-name>, value: <template>}` writes one value into the *current*
pane's `$name` scope without navigating - no history entry, no `goto`. A
dashboard that wants stock's plan-screen parity puts `device-inspector` and
`zone-inspector` beside a `floorplan`, gated with `visible_if: {exists: true}`
so each one only shows once something is selected:

```yaml
- type: floorplan
  focus: $room
  on_press: {goto: focus, params: {room: $room, zone: $zone}}
  on_select: {set: device, value: $entity}
- type: device-inspector
  entity: $device
  visible_if: {exists: true}
- type: zone-inspector
  zone: $zone
  room: $room
  visible_if: {exists: true}
```

`device-inspector` binds one entity the ordinary way (`entity:`) and shows a
toggle plus a slider chosen from the entity id's domain: `light.*` gets
brightness, `climate.*` gets temperature, `cover.*` gets position, anything
else gets no slider. `zone-inspector` takes `zone:` and/or `room:` props
(resolved the same way `focus:` is: tried as a zone id, then as a room's
zone) and shows a master toggle plus group brightness/temperature sliders for
whichever of those domains are present - it does not include stock's
ZONE/ROOM scope switch or a device-tile list; build those from `device-rows`
if a dashboard wants them.

`toggle` and `slider` normally bind one `entity:`; given the `power-chip`
selector shape instead (`entities:` a literal list, or `room:`/`zone:`/
`floor:`/`kind:`) they command the whole group via the backend's group
operations (`toggle_group`, `set_group_brightness`, `set_group_temperature`)
and reflect its aggregate state, the way a zone's master toggle does in the
stock shell.

`label` is a non-interactive caption: `text:` for a literal string, or the
same `entity:`/placeholder machinery every other component uses for a
computed one (`text: "{state}"`).

### Data, without a template language

There is no Jinja and no expression language (`docs/adr/0002`). Four narrow
mechanisms cover the ground:

* **Binding** - `entity:` on a component. Its properties are what that
  component's placeholders read. `entity: {id: ..., precision: 1, unit: "°C"}`
  carries the formatting.
* **Placeholder** - `{state}`, `{name}`, `{attributes.brightness}` read the
  node's own entity; `{sensor.outdoor_temperature.state}` names another one
  outright. Lookups only: no arithmetic, no filters, no calls.
* **Repeat** - `from:` a selector plus a `template:` child, stamped once per
  match. `over:` says what is enumerated: `devices` (default) or `places`
  (zones + lone rooms) by a `room`/`zone`/`floor`/`kind` selector, AND-ed,
  with `$entity`/`$room`/`$zone`/`$name` bound per item; `floors` (one per
  `Floor`, `$floor`/`$name`) and `rooms` (one per `Room`, `$room`/`$name`/
  `$zone`) by the same plan selector; or `entities`, which reads the
  *backend's* live snapshot instead of the plan - either a literal id list
  (`from: {entities: [light.a, light.b]}`) or every entity of one HA domain
  (`from: {domain: sensor}`), both binding `$entity`.
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

`on_press:` takes `toggle`, `back`, `none`, `{goto: <id>, params: {...}}`,
`{call: light.turn_off, data: {...}}` or `{set: <param-name>, value: <template>}`.
`goto` params are `$name` values the target pane's selectors and placeholders
can then read. `set` is narrower: it copies one value into one param of the
*current* pane without navigating - no new pane, no history entry, and no
arithmetic or lookups beyond the usual `$name` substitution (`docs/adr/0006`).
`floorplan` is the only component that fires `on_select:` today - see above.

### When it goes wrong

Structure is validated at load: an unknown `type:`, a bad span, a dangling
`goto` or a duplicate `id` refuses to boot, naming the file and line. A
missing entity at runtime draws as `inop` rather than crashing.

Reload without restarting: **SIGHUP** (`kill -HUP <pid>`) on the panel, `F5`
in the dev window. A dashboard that fails to reload leaves the running one up
and reports the error in the message strip - the panel never goes black.

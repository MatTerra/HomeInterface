"""Read a dashboard file into a :class:`~.schema.Dashboard`.

Parsing is separate from building for one reason: a dashboard has to be
checkable without a display.  Nothing here imports pygame, and every mistake
we can catch is caught here, with the line the author wrote it on - the panel
has no keyboard, so a bad file must fail loudly at load rather than quietly at
render.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .registry import CONTAINERS, known_types
from .schema import (
    REPEAT_OVER,
    ROOT_COLUMNS,
    ROOT_ROWS,
    Action,
    Binding,
    Dashboard,
    DashboardError,
    LevelRule,
    Node,
    Predicate,
    Repeat,
    Selector,
    Span,
    validate,
)

#: keys the loader consumes itself; everything else on a node is a component
#: property, passed through untouched to whatever builds the widget
RESERVED = {
    "type", "id", "columns", "rows", "entity", "precision", "unit",
    "visible_if", "levels", "from", "over", "template", "overflow",
    "children", "on_press", "on_select",
}
_LINE = "__line__"


class _LineLoader(yaml.SafeLoader):
    """SafeLoader that stamps every mapping with the line it started on."""


def _mapping_with_line(loader: _LineLoader, node: yaml.MappingNode) -> dict[str, Any]:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=True)
    mapping.setdefault(_LINE, node.start_mark.line + 1)
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_with_line)


def _strip(value: Any) -> Any:
    """Drop the loader's line stamps from data handed on to components."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != _LINE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def load_dashboard(path: str | Path) -> Dashboard:
    """Parse and validate the dashboard at ``path``."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise DashboardError(f"cannot read dashboard: {exc}", source=str(file)) from None
    return dashboard_from_text(text, source=str(file))


def dashboard_from_text(text: str, *, source: str | None = None) -> Dashboard:
    try:
        data = yaml.load(text, Loader=_LineLoader) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise DashboardError(f"invalid YAML: {getattr(exc, 'problem', exc)}", source=source,
                             line=mark.line + 1 if mark else None) from None
    return dashboard_from_dict(data, source=source)


def dashboard_from_dict(data: dict[str, Any], *, source: str | None = None) -> Dashboard:
    if not isinstance(data, dict):
        raise DashboardError("dashboard root must be a mapping", source=source)
    if "root" not in data:
        raise DashboardError("dashboard needs a root: node", source=source)
    root = _node(data["root"], source, default_span=Span(ROOT_COLUMNS, ROOT_ROWS))
    start = data.get("start")
    if start is not None and not isinstance(start, str):
        raise DashboardError(f"start must be a node id, got {start!r}", source=source)
    dashboard = Dashboard(root=root, start=start, source=source)
    validate(dashboard, known_types(), set(CONTAINERS))
    return dashboard


# -- nodes ---------------------------------------------------------------
def _node(data: Any, source: str | None, *, default_span: Span | None = None) -> Node:
    if not isinstance(data, dict):
        raise DashboardError(f"a node must be a mapping, got {type(data).__name__}", source=source)
    line = data.get(_LINE)
    kind = data.get("type")
    if not isinstance(kind, str) or not kind:
        raise DashboardError("a node needs a type:", source=source, line=line)

    span = default_span or Span()
    has_span = "columns" in data or "rows" in data
    if has_span:
        span = Span(_number("columns", data.get("columns", span.columns), source, line),
                    _number("rows", data.get("rows", span.rows), source, line))

    children = data.get("children") or []
    if not isinstance(children, list):
        raise DashboardError("children: must be a list of nodes", source=source, line=line)

    node = Node(
        type=kind,
        id=data.get("id"),
        span=span,
        props=_strip({k: v for k, v in data.items() if k not in RESERVED and k != _LINE}),
        children=[_node(child, source) for child in children],
        binding=_binding(data, source, line),
        visible_if=_predicate(data.get("visible_if"), source, line) if "visible_if" in data else None,
        levels=_levels(data.get("levels"), source, line),
        repeat=_repeat(data, source, line),
        overflow=str(data.get("overflow", "auto")),
        action=_action(data.get("on_press"), source, line),
        select_action=_action(data.get("on_select"), source, line),
        has_span=has_span or default_span is not None,
        line=line,
    )
    if node.id is not None and not isinstance(node.id, str):
        raise DashboardError(f"id must be a string, got {node.id!r}", source=source, line=line)
    return node


def _number(name: str, value: Any, source: str | None, line: int | None) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DashboardError(f"{name} must be a number, got {value!r}",
                             source=source, line=line) from None


def _binding(data: dict[str, Any], source: str | None, line: int | None) -> Binding | None:
    entity = data.get("entity")
    if entity is None:
        return None
    if isinstance(entity, str):
        return Binding(entity, precision=data.get("precision"), unit=data.get("unit"))
    if isinstance(entity, dict):
        entity_id = entity.get("id") or entity.get("entity")
        if not isinstance(entity_id, str):
            raise DashboardError("entity: mapping needs an id:", source=source, line=line)
        return Binding(entity_id, precision=entity.get("precision", data.get("precision")),
                       unit=entity.get("unit", data.get("unit")))
    raise DashboardError(f"entity must be an id or a mapping, got {entity!r}",
                         source=source, line=line)


def _predicate(data: Any, source: str | None, line: int | None) -> Predicate:
    if not isinstance(data, dict):
        raise DashboardError(f"a condition must be a mapping, got {data!r}",
                             source=source, line=line)
    kinds = [k for k in ("state", "above", "below", "exists") if k in data]
    if len(kinds) != 1:
        raise DashboardError(
            "a condition asks exactly one of state, above, below, exists"
            f" (got {', '.join(kinds) or 'none'})",
            source=source, line=data.get(_LINE, line),
        )
    kind = kinds[0]
    return Predicate(kind=kind, value=data[kind], entity=data.get("entity"),
                     attribute=str(data.get("attribute", "state")))


def _levels(data: Any, source: str | None, line: int | None) -> tuple[LevelRule, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise DashboardError("levels: must be a list of rules", source=source, line=line)
    rules: list[LevelRule] = []
    for item in data:
        if not isinstance(item, dict) or "level" not in item:
            raise DashboardError(f"a level rule needs a level:, got {item!r}",
                                 source=source, line=line)
        body = {k: v for k, v in item.items() if k != "level"}
        has_condition = any(k in body for k in ("state", "above", "below", "exists"))
        rules.append(LevelRule(
            level=str(item["level"]),
            predicate=_predicate(body, source, line) if has_condition else None,
        ))
    return tuple(rules)


def _repeat(data: dict[str, Any], source: str | None, line: int | None) -> Repeat | None:
    raw = data.get("from")
    if raw is None:
        if "template" in data:
            raise DashboardError("template: needs a from: selector to repeat over",
                                 source=source, line=line)
        return None
    if not isinstance(raw, dict):
        raise DashboardError(f"from: must be a selector mapping, got {raw!r}",
                             source=source, line=line)
    unknown = set(raw) - {"room", "zone", "floor", "kind", "entities", "domain", _LINE}
    if unknown:
        raise DashboardError(
            f"a selector matches room, zone, floor or kind (or entities/domain for"
            f" over: entities); got {', '.join(sorted(unknown))}",
            source=source, line=line,
        )
    template = data.get("template")
    if template is None:
        raise DashboardError("from: needs a template: node to stamp out",
                             source=source, line=line)
    over = str(data.get("over", "devices"))
    if over not in REPEAT_OVER:
        raise DashboardError(
            f"over: must be one of {', '.join(REPEAT_OVER)}, got {over!r}",
            source=source, line=line,
        )
    selector = Selector(room=raw.get("room"), zone=raw.get("zone"),
                        floor=raw.get("floor"), kind=raw.get("kind"))
    entities = _entity_list(raw.get("entities"), source, line)
    domain = raw.get("domain")
    if domain is not None and not isinstance(domain, str):
        raise DashboardError(f"domain: must be a string, got {domain!r}",
                             source=source, line=line)
    if over == "entities" and not entities and not domain:
        raise DashboardError(
            "over: entities needs an entities: list or a domain: selector",
            source=source, line=line,
        )
    return Repeat(selector=selector, template=_node(template, source), over=over,
                  entities=entities, domain=domain)


def _entity_list(raw: Any, source: str | None, line: int | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(e, str) for e in raw):
        raise DashboardError(f"entities: must be a list of entity ids, got {raw!r}",
                             source=source, line=line)
    return tuple(raw)


def _action(data: Any, source: str | None, line: int | None) -> Action | None:
    if data is None:
        return None
    if isinstance(data, str):
        if data in ("back", "none"):
            return Action(kind=data)
        if data == "toggle":
            return Action(kind="toggle")
        raise DashboardError(
            f"on_press {data!r} takes no argument only as toggle, back or none",
            source=source, line=line,
        )
    if not isinstance(data, dict):
        raise DashboardError(f"on_press must be a name or a mapping, got {data!r}",
                             source=source, line=line)
    body = {k: v for k, v in data.items() if k != _LINE}
    if "goto" in body:
        return Action(kind="goto", target=str(body["goto"]),
                      params=_strip(body.get("params") or {}))
    if "call" in body:
        return Action(kind="call", service=str(body["call"]),
                      target=body.get("entity"), data=_strip(body.get("data") or {}))
    if "toggle" in body:
        return Action(kind="toggle", target=str(body["toggle"]))
    if "set" in body:
        return Action(kind="set", target=str(body["set"]),
                      params=_strip({"value": body.get("value", "")}))
    raise DashboardError("on_press needs one of toggle, goto, call, set, back, none",
                         source=source, line=line)

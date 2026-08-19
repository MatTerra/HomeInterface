"""The ``type:`` table: names an author may write, and what builds them.

Containers are handled by the layout engine itself; every other name resolves
to a builder that returns one widget.  Built-ins go through this same table -
there is no separate path for them, so anything registered here behaves
exactly like a component that ships with the app.

The names are declared as data, apart from the builders, so a dashboard can be
validated without importing pygame.
"""

from __future__ import annotations

from typing import Any, Callable

#: containers: nodes that hold other nodes and divide their rectangle.
#: ``chips`` is ``cols`` with compact children - a chip row is this UI's tab
#: strip, and writing it out as ten compact buttons every time reads badly.
CONTAINERS: tuple[str, ...] = ("grid", "rows", "cols", "tabs", "chips")

#: components that ship with the app.  A name here without a builder is a
#: programming error, caught by :func:`builder` at build time.
COMPONENTS: tuple[str, ...] = (
    "floorplan", "places", "device-rows", "toggle", "slider", "tile", "readout",
    "arc-gauge", "bar-gauge", "lamp", "messages", "clock", "panel", "button",
    "power-chip", "spacer", "label", "attr-list", "link-status",
    "device-inspector", "zone-inspector",
)

#: name -> builder.  Filled by :mod:`.components` on import.
_BUILDERS: dict[str, Callable[..., Any]] = {}


def register(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Bind ``name`` to the decorated builder."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _BUILDERS[name] = fn
        return fn

    return decorator


def builder(name: str) -> Callable[..., Any] | None:
    return _BUILDERS.get(name)


def known_types() -> set[str]:
    """Every ``type:`` a dashboard may name."""
    return set(CONTAINERS) | set(COMPONENTS) | set(_BUILDERS)

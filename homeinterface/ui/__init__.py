"""Widget toolkit: resolution-independent, touch-first, avionics-styled."""

from .base import Pressable, UIContext, Widget, WidgetGroup
from .controls import Button, Slider, TabStrip, ToggleButton
from .indicators import ArcGauge, BarGauge, Clock, EntityTile, MessageStrip, Panel, Readout, StatusLamp

__all__ = [
    "ArcGauge", "BarGauge", "Button", "Clock", "EntityTile", "MessageStrip",
    "Panel", "Pressable", "Readout", "Slider", "StatusLamp", "TabStrip",
    "UIContext", "Widget", "WidgetGroup",
]

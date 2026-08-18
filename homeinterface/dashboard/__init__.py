"""Dashboards: a shell whose screens are declared in a YAML file."""

from .build import DashboardScreen
from .loader import dashboard_from_dict, dashboard_from_text, load_dashboard
from .schema import Dashboard, DashboardError, Node, Span

__all__ = [
    "Dashboard", "DashboardError", "DashboardScreen", "Node", "Span",
    "dashboard_from_dict", "dashboard_from_text", "load_dashboard",
]

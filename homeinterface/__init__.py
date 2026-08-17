"""HomeInterface - a resolution-independent house control panel in pygame.

Styled after Airbus ECAM / Embraer EICAS displays: dark ground, semantic
colour, angular vector geometry, no decoration that does not carry meaning.
"""

from .config import load_config
from .scaling import Box, Viewport
from .theme import Theme

__version__ = "0.1.0"
__all__ = ["Box", "Theme", "Viewport", "load_config"]

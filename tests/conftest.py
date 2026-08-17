"""Shared pytest fixtures.

Every test that touches pygame must run headless: SDL is pointed at the
"dummy" video driver *before* pygame is imported anywhere, and pygame/font
are initialised once for the whole session.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402  (must follow the SDL env vars above)
import pytest


@pytest.fixture(scope="session", autouse=True)
def _headless_pygame():
    pygame.init()
    pygame.font.init()
    yield
    pygame.quit()

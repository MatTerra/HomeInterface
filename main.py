"""Entry point: ``python main.py [--fullscreen] [--config config/app.yaml]``."""

from __future__ import annotations

import sys

from homeinterface.app import main

if __name__ == "__main__":
    sys.exit(main())

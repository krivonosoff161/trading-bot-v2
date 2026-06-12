# -*- coding: utf-8 -*-
"""Start the local Strategy Research Lab dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.dashboard_server import main  # noqa: E402


if __name__ == "__main__":
    main()

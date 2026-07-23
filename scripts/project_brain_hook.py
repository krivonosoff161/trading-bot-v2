"""Codex lifecycle hook entrypoint; always fail-open into explicit degraded mode."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.project_brain.codex_hooks import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

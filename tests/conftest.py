from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Credential-bearing local files must never influence tests. This is set before
# pytest imports test modules, including modules with import-time dotenv loads.
os.environ["TRADING_BOT_DOTENV_AUTOLOAD"] = "0"

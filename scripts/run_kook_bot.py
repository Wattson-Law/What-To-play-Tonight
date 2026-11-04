from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coop_night_picker.kook_bot import main

if __name__ == "__main__":
    main()

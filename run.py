"""Launcher: run with `python run.py` from the project root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aircatch.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

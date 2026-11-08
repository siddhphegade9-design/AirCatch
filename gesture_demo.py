"""Launcher for the live gesture test window (adds src/ to Python's path).

  python gesture_demo.py --list-cameras
  python gesture_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aircatch.gestures.demo import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

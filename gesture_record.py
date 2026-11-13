"""Launcher: record prompted gestures for tuning (adds src/ to Python's path).

  python gesture_record.py
  python gesture_record.py --replay aircatch-recording-....json.gz
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aircatch.gestures.record import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

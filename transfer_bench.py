"""Launcher for the speed benchmark (adds src/ to Python's path).

  python transfer_bench.py --size-mb 512
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aircatch.transfer.benchmark import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

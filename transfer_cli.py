"""Launcher for the transfer command-line tool (adds src/ to Python's path).

  python transfer_cli.py receive --dir received --port 5001
  python transfer_cli.py send --host 127.0.0.1 --port 5001 somefile.zip
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from aircatch.transfer.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

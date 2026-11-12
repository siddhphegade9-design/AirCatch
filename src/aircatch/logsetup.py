"""A log file so a problem on someone else's PC can be understood afterwards.

It records what AirCatch decided and what the network answered (device names, file NAMES and
sizes, addresses, error codes, gesture events). It never records file contents, pairing codes,
certificates or keys. It lives next to the device's identity: <config dir>/aircatch.log, and a
crash report (if Windows ever kills the process) goes to <config dir>/crash.log.
"""
from __future__ import annotations

import faulthandler
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_crash_file = None


def setup_logging(config_dir: Path) -> Path | None:
    """Start writing aircatch.log. Safe to call twice. Returns the log path (None if unwritable)."""
    global _crash_file
    logger = logging.getLogger("aircatch")
    path = Path(config_dir) / "aircatch.log"
    if any(getattr(h, "_aircatch_file", False) for h in logger.handlers):
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    except OSError:
        return None
    handler._aircatch_file = True                      # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def log_uncaught(kind, value, trace):
        logger.critical("uncaught exception", exc_info=(kind, value, trace))
        sys.__excepthook__(kind, value, trace)
    sys.excepthook = log_uncaught
    try:
        _crash_file = open(Path(config_dir) / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file)
    except OSError:
        pass
    logger.info("---- AirCatch started (log: %s) ----", path)
    return path

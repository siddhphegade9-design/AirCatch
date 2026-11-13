"""Safe file names: never trust a name that arrives over the network."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
             "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(name: str, max_length: int = 150) -> str:
    """Return a safe file name (no folders, no Windows-forbidden characters)."""
    name = name.replace("\\", "/").split("/")[-1]        # drop any folder part
    name = unicodedata.normalize("NFC", name)
    # invisible formatting characters can disguise a name: "photo<right-to-left>gpj.exe" is shown
    # as "photoexe.jpg". Drop them all (Unicode category Cf: bidi overrides, zero-width, ...).
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co"))
    name = _INVALID_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "file"
    stem, ext = os.path.splitext(name)
    ext = ext[:20]
    # Windows device names are reserved whatever follows the first dot: NUL.tar.gz is still NUL
    if stem.split(".")[0].strip(" ").upper() in _RESERVED:
        stem = "_" + stem
    stem = stem[: max(1, max_length - len(ext))].rstrip(" .") or "file"
    return stem + ext


def reserve_destination(directory: Path, filename: str) -> Path:
    """Atomically claim a free name in `directory` (adds ' (1)', ' (2)'... if taken).

    The empty placeholder is created with mode 'x' (fail if exists), so an existing
    file can never be overwritten, even if two transfers finish at the same moment.
    """
    stem, ext = os.path.splitext(filename)
    for n in range(10_000):
        candidate = directory / (filename if n == 0 else f"{stem} ({n}){ext}")
        try:
            with open(candidate, "xb"):
                pass
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError("could not find a free file name")

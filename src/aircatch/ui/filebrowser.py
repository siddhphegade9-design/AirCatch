"""A small, safe folder browser for the gesture-driven file explorer.

Knows nothing about Qt, cameras or gestures: it lists folders, keeps a highlighted row, and says
what happened when a row is chosen. The screen and the gestures both just call move / enter / back.

  * the first view is a list of "places" (Desktop, Documents, Downloads, drives ...);
  * hidden and system files are not shown, folders come first, then files (A-Z);
  * a folder that cannot be read says so instead of crashing, and very long folders are cut off;
  * nothing is ever opened or run: choosing a file only returns its path.
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass
from pathlib import Path

from aircatch.ui.thumbnails import file_kind, human_size

MAX_ENTRIES = 400
_HIDDEN_WIN = 0x2 | 0x4          # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    is_dir: bool
    detail: str = ""             # size for files, "Folder" / "Drive" for folders
    badge: str = ""              # short file-type label for the icon


def default_places() -> list[Entry]:
    home = Path.home()
    candidates = [("Desktop", [home / "Desktop", home / "OneDrive" / "Desktop"]),
                  ("Documents", [home / "Documents", home / "OneDrive" / "Documents"]),
                  ("Downloads", [home / "Downloads"]),
                  ("Pictures", [home / "Pictures", home / "OneDrive" / "Pictures"]),
                  ("Videos", [home / "Videos"]), ("Music", [home / "Music"])]
    places = []
    for label, paths in candidates:
        path = next((p for p in paths if p.is_dir()), None)
        if path is not None:
            places.append(Entry(label, path, True, "Folder"))
    places.append(Entry("Home", home, True, "Folder"))
    places.extend(Entry(f"Drive {d}", Path(d + "\\"), True, "Drive") for d in _windows_drives())
    return places


def _windows_drives() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()      # asks Windows; never touches the disks
    except Exception:
        return []
    return [f"{letter}:" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith(".") or entry.name.startswith("$") or entry.name.lower() == "desktop.ini":
        return True
    try:
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _HIDDEN_WIN)
    except OSError:
        return True


class FileBrowser:
    def __init__(self, places: list[Entry] | None = None) -> None:
        self._places = places if places is not None else default_places()
        self.path: Path | None = None          # None = the list of places
        self.entries: list[Entry] = []
        self.index = 0
        self.note = ""                         # e.g. "This folder is empty."
        self._trail: list[tuple[Path | None, int]] = []     # where we came from, to restore the highlight
        self.show_places()

    # ------------------------------------------------------------------ views
    @property
    def title(self) -> str:
        return "This computer" if self.path is None else str(self.path)

    @property
    def highlighted(self) -> Entry | None:
        return self.entries[self.index] if 0 <= self.index < len(self.entries) else None

    def show_places(self) -> None:
        self.path, self.entries, self.index, self.note = None, list(self._places), 0, ""
        self._trail.clear()

    def _load(self, path: Path) -> bool:
        folders: list[Entry] = []
        files: list[Entry] = []
        note = ""
        try:
            with os.scandir(path) as it:
                for item in it:
                    if _is_hidden(item):
                        continue
                    try:
                        is_dir = item.is_dir()
                        if is_dir:
                            folders.append(Entry(item.name, Path(item.path), True, "Folder"))
                        elif item.is_file():
                            kind, badge = file_kind(item.name)
                            files.append(Entry(item.name, Path(item.path), False,
                                               human_size(item.stat().st_size), badge))
                    except OSError:
                        continue
        except OSError:
            self.note = "AirCatch cannot open this folder."
            return False
        folders.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())
        entries = folders + files
        if len(entries) > MAX_ENTRIES:
            entries, note = entries[:MAX_ENTRIES], f"Showing the first {MAX_ENTRIES} items."
        elif not entries:
            note = "This folder is empty."
        self.path, self.entries, self.index, self.note = path, entries, 0, note
        return True

    # ------------------------------------------------------------------ moving around
    def move(self, delta: int) -> None:
        if self.entries:
            self.index = max(0, min(len(self.entries) - 1, self.index + delta))

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self.index = index

    def open_folder(self) -> bool:
        """Go into the highlighted folder. False if the highlight is a file or the folder is unreadable."""
        entry = self.highlighted
        if entry is None or not entry.is_dir:
            return False
        before = (self.path, self.index)
        if self._load(entry.path):
            self._trail.append(before)
            return True
        return False

    def enter(self) -> Path | None:
        """Choose the highlighted row: a folder is opened (returns None); a file is returned."""
        entry = self.highlighted
        if entry is None:
            return None
        if entry.is_dir:
            self.open_folder()
            return None
        return entry.path

    def back(self) -> bool:
        """One level up (to the places list from a top-level folder). False if already at the top."""
        if self.path is None:
            return False
        if self._trail:
            parent, index = self._trail.pop()
            if parent is None:
                trail = list(self._trail)
                self.show_places()
                self._trail = trail
            elif not self._load(parent):
                return False
            self.index = min(index, max(0, len(self.entries) - 1))
            return True
        parent = self.path.parent
        if parent == self.path:
            self.show_places()
        elif not self._load(parent):
            self.show_places()
        return True

from pathlib import Path

import pytest

from aircatch.ui.filebrowser import MAX_ENTRIES, Entry, FileBrowser


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "Desktop" / "Trips" / "Goa").mkdir(parents=True)
    (tmp_path / "Desktop" / "notes.txt").write_text("hi")
    (tmp_path / "Desktop" / "beach.png").write_bytes(b"x" * 2048)
    (tmp_path / "Desktop" / ".secret").write_text("no")
    (tmp_path / "Desktop" / "desktop.ini").write_text("no")
    (tmp_path / "Desktop" / "Trips" / "plan.pdf").write_bytes(b"%PDF")
    (tmp_path / "Empty").mkdir()
    places = [Entry("Desktop", tmp_path / "Desktop", True, "Folder"), Entry("Empty", tmp_path / "Empty", True, "Folder")]
    return tmp_path, FileBrowser(places)


def names(b):
    return [e.name for e in b.entries]


def test_starts_at_places_and_lists_folders_first_without_hidden_files(tree):
    root, b = tree
    assert b.title == "This computer" and names(b) == ["Desktop", "Empty"]
    assert b.enter() is None and b.path == root / "Desktop"
    assert names(b) == ["Trips", "beach.png", "notes.txt"]            # folders first, then A-Z, nothing hidden


def test_move_stays_inside_the_list(tree):
    _, b = tree
    b.move(-3)
    assert b.index == 0
    b.move(10)
    assert b.index == 1
    b.move(-1)
    assert b.index == 0


def test_choosing_a_file_returns_it_and_choosing_a_folder_goes_in(tree):
    root, b = tree
    b.enter()                                   # Desktop
    b.move(1)                                   # beach.png
    assert b.enter() == root / "Desktop" / "beach.png"
    b.move(-1)                                  # Trips
    assert b.enter() is None and b.path == root / "Desktop" / "Trips"
    assert names(b) == ["Goa", "plan.pdf"]


def test_back_restores_the_highlight_and_ends_at_places(tree):
    root, b = tree
    b.enter()
    b.move(2)                                   # notes.txt
    b.move(-2)                                  # Trips
    b.enter()
    assert b.back() and b.path == root / "Desktop" and b.highlighted.name == "Trips"
    assert b.back() and b.path is None
    assert not b.back()                         # nowhere further up


def test_open_folder_only_works_on_folders(tree):
    _, b = tree
    b.enter()
    b.move(1)
    assert not b.open_folder()
    b.move(-1)
    assert b.open_folder()


def test_empty_and_unreadable_folders_say_so_instead_of_failing(tree, tmp_path):
    root, b = tree
    b.move(1)
    b.enter()
    assert b.path == root / "Empty" and b.entries == [] and "empty" in b.note.lower()
    assert b.enter() is None and b.highlighted is None
    b.back()
    gone = FileBrowser([Entry("Gone", root / "does-not-exist", True, "Folder")])
    gone.enter()
    assert gone.path is None and "cannot open" in gone.note.lower()      # stays where it was


def test_huge_folders_are_cut_off(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(MAX_ENTRIES + 25):
        (big / f"f{i:04}.txt").write_text("")
    b = FileBrowser([Entry("big", big, True, "Folder")])
    b.enter()
    assert len(b.entries) == MAX_ENTRIES and "first" in b.note


def test_default_places_always_include_home():
    b = FileBrowser()
    assert any(e.name == "Home" and e.path == Path.home() for e in b.entries)

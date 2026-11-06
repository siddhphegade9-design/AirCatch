from aircatch.transfer.filenames import reserve_destination, sanitize_filename


def test_strips_folders_and_traversal():
    assert sanitize_filename("../../evil.txt") == "evil.txt"
    assert sanitize_filename("..\\..\\Windows\\evil.txt") == "evil.txt"
    assert sanitize_filename("/etc/passwd") == "passwd"


def test_replaces_forbidden_characters():
    assert sanitize_filename('a<b>c:d"e|f?g*h.txt') == "a_b_c_d_e_f_g_h.txt"


def test_reserved_windows_names():
    assert sanitize_filename("CON.txt") == "_CON.txt"
    assert sanitize_filename("nul") == "_nul"


def test_trailing_dots_spaces_and_empty():
    assert sanitize_filename("report.pdf. ") == "report.pdf"
    assert sanitize_filename("..") == "file"
    assert sanitize_filename("   ") == "file"
    assert sanitize_filename("") == "file"


def test_long_names_are_shortened_and_keep_extension():
    name = sanitize_filename("x" * 500 + ".zip")
    assert name.endswith(".zip") and len(name) <= 150


def test_unicode_name_is_kept():
    assert sanitize_filename("चित्र.png") == "चित्र.png"


def test_reserve_never_overwrites(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    first = reserve_destination(tmp_path, "a.txt")
    second = reserve_destination(tmp_path, "a.txt")
    assert first.name == "a (1).txt"
    assert second.name == "a (2).txt"
    assert (tmp_path / "a.txt").read_text() == "old"

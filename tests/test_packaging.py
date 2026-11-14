"""Milestone 11: the pieces that make AirCatch an installable Windows program.

What can be checked without Windows: the version, the built-in self-test (including two devices
pairing and sending a file inside one process), the first-start screen, and that the packaging
files agree with each other. The installer itself can only be tested on Windows.
"""
import json
import re
import sys
from pathlib import Path

import pytest

import aircatch
from aircatch import app as app_module
from tests.test_controller import make_controller

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_is_a_plain_three_part_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", aircatch.__version__)


def test_version_option_prints_and_exits_without_starting_the_app(capsys):
    assert app_module.main(["AirCatch", "--version"]) == 0
    assert capsys.readouterr().out.strip() == f"AirCatch {aircatch.__version__}"


def test_options_are_taken_out_of_the_command_line():
    take = app_module._take_option
    assert take(["a", "--self-test", "r.json", "--port", "5"], "--self-test", True) == (["a", "--port", "5"], "r.json")
    assert take(["a", "--version"], "--version", False) == (["a"], "")
    assert take(["a", "--port", "5"], "--self-test", True) == (["a", "--port", "5"], None)
    assert take(["a", "--self-test"], "--self-test", True) == (["a"], "")            # value forgotten


def test_a_program_without_a_console_can_still_print(monkeypatch):
    """A windowed .exe has no stdout or stderr at all; a stray print() must not crash it."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app_module._prepare_process()
    print("this would have crashed")
    assert sys.stdout is not None and sys.stderr is not None


def test_two_devices_pair_and_send_a_file_each_way_inside_one_process():
    """The heart of the self-test the packaged program runs after every build."""
    from aircatch.selftest import pair_and_transfer
    summary = pair_and_transfer(size=400_000)
    assert "both files arrived identical" in summary


def test_the_first_start_screen_shows_once_and_stays_away(qapp, tmp_path):
    first = make_controller(qapp, tmp_path)
    try:
        assert first.showWelcome                         # a new PC: explain camera, firewall, pairing
        first.dismissWelcome()
        assert not first.showWelcome
        first.toggleReducedMotion()                      # other settings must not bring it back
    finally:
        first.shutdown()
    again = make_controller(qapp, tmp_path)
    try:
        assert not again.showWelcome and again.reducedMotion
        again.showWelcomeAgain()                         # the Help link
        assert again.showWelcome
        assert again.appVersion == aircatch.__version__
    finally:
        again.shutdown()


def test_a_damaged_settings_file_just_shows_the_welcome_again(qapp, tmp_path):
    ctl = make_controller(qapp, tmp_path)
    ctl.dismissWelcome()
    path = ctl._ui_settings_path()
    ctl.shutdown()
    path.write_text("{ not json", "utf-8")
    ctl = make_controller(qapp, tmp_path)
    try:
        assert ctl.showWelcome
    finally:
        ctl.shutdown()


def test_the_packaging_files_agree_with_the_code():
    spec = (ROOT / "packaging" / "aircatch.spec").read_text("utf-8")
    iss = (ROOT / "packaging" / "installer.iss").read_text("utf-8")
    build = (ROOT / "packaging" / "build.ps1").read_text("utf-8")
    app = (ROOT / "src" / "aircatch" / "app.py").read_text("utf-8")
    # everything the program reads at run time is in the recipe
    assert '"main.qml"' in spec and '"assets"' in spec and 'collect_data_files("mediapipe")' in spec
    assert "console=False" in spec and "upx=False" in spec
    for needed in ("main.qml", "aircatch-icon.png", "aircatch-logo.png"):
        assert list((ROOT / "src" / "aircatch" / "ui").rglob(needed)), needed
    assert (ROOT / "packaging" / "aircatch.ico").stat().st_size > 10_000
    # the installer and the program use the same mutex name, .exe name and data folder
    mutex = re.search(r'CreateMutexW\(None, False, "([^"]+)"\)', app).group(1)
    assert f"AppMutex={mutex}" in iss
    assert '#define AppExe "AirCatch.exe"' in iss and 'name="AirCatch"' in spec
    assert r"{localappdata}\AirCatch" in iss
    assert "profile=private" in iss and "profile=public" not in iss.lower()       # never open on public networks
    assert re.search(r"AppId=\{\{[0-9A-F-]{36}\}", iss)
    # the build refuses the PySide6 version that crashes the app, and tests the result
    assert '6.12' in build and "--self-test" in build
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    assert re.search(r"PySide6>=6\.7,<6\.12", requirements)


def test_the_changelog_describes_this_version():
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    assert re.search(rf"^## \[?{re.escape(aircatch.__version__)}\]?", text, re.M)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_on_windows_the_installer_mutex_is_created():
    assert app_module._prepare_process()

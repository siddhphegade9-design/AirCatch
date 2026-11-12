"""Run Qt headless (no window, no GPU) so the UI tests work anywhere, including a build server."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QGuiApplication.instance() or QGuiApplication([])

from PySide6.QtGui import QColor, QImage

from aircatch.ui.frame_provider import FrameProvider


def test_provider_returns_placeholder_before_first_frame():
    img = FrameProvider().requestImage("frame", None, None)
    assert not img.isNull()


def test_provider_returns_latest_frame():
    p = FrameProvider()
    frame = QImage(4, 4, QImage.Format.Format_RGB888)
    frame.fill(QColor("red"))
    p.set_image(frame)
    assert p.requestImage("frame", None, None).width() == 4


def test_emitting_qt_signals_does_not_wear_out_true_and_false():
    """PySide6 6.12.0 on Python 3.11 loses one reference to True on every signal emission; after a
    few thousand the interpreter aborts ("deallocating True or False"). The app emits signals for
    every camera frame, so that version crashes it within seconds. requirements.txt excludes it;
    this test fails at once if a bad version is installed anyway."""
    import sys

    from PySide6.QtCore import QObject, Signal

    class Probe(QObject):
        plain = Signal()
        flag = Signal(bool)

    probe = Probe()
    before = sys.getrefcount(True), sys.getrefcount(False)
    for _ in range(200):
        probe.plain.emit()
        probe.flag.emit(True)
    after = sys.getrefcount(True), sys.getrefcount(False)
    assert abs(after[0] - before[0]) < 50 and abs(after[1] - before[1]) < 50, \
        "this PySide6 version corrupts True/False reference counts: install PySide6>=6.7,<6.12"

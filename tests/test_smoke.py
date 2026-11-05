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

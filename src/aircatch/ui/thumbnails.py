"""File previews: what the file looks like on the sender, and what arrives in the offer.

Pictures get a small JPEG preview (at most 20 KB) that travels inside the offer, so the receiver
sees the real image before accepting. Everything else gets a file-type badge instead.
Previews that arrive from the network are untrusted image data: they are decoded with a
size-bounded reader and anything oversized or unreadable is simply ignored.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from aircatch.transfer.protocol import MAX_THUMBNAIL_BYTES

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_SOURCE_BYTES = 100 * 1024 * 1024        # do not even try to preview bigger pictures
MAX_SOURCE_PIXELS = 100_000_000
MAX_INCOMING_SIDE = 1024                    # decoded previews from the network are tiny; refuse more

_KINDS = {
    "image": IMAGE_EXTENSIONS | {".svg", ".tif", ".tiff", ".heic"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
    "pdf": {".pdf"},
    "document": {".doc", ".docx", ".txt", ".md", ".rtf", ".odt"},
    "sheet": {".xls", ".xlsx", ".csv", ".ods"},
    "slides": {".ppt", ".pptx", ".odp"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
}


def file_kind(name: str) -> tuple[str, str]:
    """('pdf', 'PDF') for 'report.pdf'. The badge is the upper-case extension (max 4 letters)."""
    ext = Path(name).suffix.lower()
    badge = ext.lstrip(".").upper()[:4] or "FILE"
    for kind, extensions in _KINDS.items():
        if ext in extensions:
            return kind, badge
    return "other", badge


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def load_preview(path: str | Path, max_side: int = 320) -> QImage:
    """A downscaled picture for the card on this screen, or a null image if not a usable picture."""
    path = Path(path)
    try:
        if path.suffix.lower() not in IMAGE_EXTENSIONS or path.stat().st_size > MAX_SOURCE_BYTES:
            return QImage()
    except OSError:
        return QImage()
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not reader.canRead() or not size.isValid() or size.width() * size.height() > MAX_SOURCE_PIXELS:
        return QImage()
    if max(size.width(), size.height()) > max_side:
        reader.setScaledSize(size.scaled(QSize(max_side, max_side), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return QImage() if image.isNull() else image


def _flatten(image: QImage) -> QImage:
    """Transparent areas become dark navy instead of black-after-JPEG."""
    flat = QImage(image.size(), QImage.Format.Format_RGB32)
    flat.fill(QColor("#0e1430"))
    painter = QPainter(flat)
    painter.drawImage(0, 0, image)
    painter.end()
    return flat


def jpeg_for_offer(image: QImage, limit: int = MAX_THUMBNAIL_BYTES) -> bytes:
    """Smallest-effort JPEG that fits the offer limit, or b'' if even a tiny one does not."""
    if image.isNull():
        return b""
    flat = _flatten(image)
    for side in (256, 192, 128):
        scaled = flat if max(flat.width(), flat.height()) <= side else flat.scaled(
            side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for quality in (75, 60, 45, 30):
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if scaled.save(buffer, "JPEG", quality) and data.size() <= limit:
                return bytes(data)
    return b""


def decode_incoming_preview(data: bytes) -> QImage:
    """Decode a preview received from another device, with strict bounds. Null if anything is off."""
    if not data or len(data) > MAX_THUMBNAIL_BYTES:
        return QImage()
    array = QByteArray(data)
    buffer = QBuffer(array)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if not reader.canRead() or not size.isValid() or max(size.width(), size.height()) > MAX_INCOMING_SIDE:
        return QImage()
    image = reader.read()
    return QImage() if image.isNull() else image

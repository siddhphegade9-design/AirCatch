"""File previews: what the card shows, and what is safe to accept from the network."""
import io
import os

import pytest
from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from aircatch.transfer.protocol import (MAX_THUMBNAIL_BYTES, decode_thumbnail, encode_thumbnail)
from aircatch.ui.thumbnails import (decode_incoming_preview, file_kind, human_size, jpeg_for_offer,
                                    load_preview)


def jpeg(size, quality=70, noisy=False):
    img = (Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if noisy
           else Image.new("RGB", size, (30, 140, 220)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_file_kind_and_size_text():
    assert file_kind("holiday.JPG") == ("image", "JPG")
    assert file_kind("report.pdf") == ("pdf", "PDF")
    assert file_kind("movie.mkv")[0] == "video"
    assert file_kind("archive.tar")[0] == "archive"
    assert file_kind("README") == ("other", "FILE")
    assert file_kind("weird.abcdefgh") == ("other", "ABCD")
    assert human_size(0) == "0 B" and human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 ** 2) == "5.0 MB" and human_size(3 * 1024 ** 3) == "3.0 GB"


def test_load_preview_downscales_big_pictures_and_ignores_the_rest(qapp, tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGB", (3000, 2000), (10, 200, 90)).save(big)
    image = load_preview(big)
    assert not image.isNull() and max(image.width(), image.height()) <= 320
    assert image.width() > image.height()                       # aspect ratio kept
    text = tmp_path / "a.txt"
    text.write_text("hi")
    assert load_preview(text).isNull()
    assert load_preview(tmp_path / "missing.png").isNull()
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"this is not a png")
    assert load_preview(fake).isNull()


def test_offer_preview_always_fits_the_limit(qapp):
    noisy = QImage.fromData(jpeg((900, 900), 95, noisy=True))   # worst case: random noise
    data = jpeg_for_offer(noisy)
    assert data.startswith(b"\xff\xd8\xff") and 0 < len(data) <= MAX_THUMBNAIL_BYTES
    assert encode_thumbnail(data) is not None                  # and the protocol accepts it
    assert jpeg_for_offer(QImage()) == b""


def test_transparent_pictures_do_not_turn_black(qapp):
    clear = QImage(100, 100, QImage.Format.Format_ARGB32)
    clear.fill(0)                                               # fully transparent
    out = QImage.fromData(jpeg_for_offer(clear))
    pixel = out.pixelColor(50, 50)
    assert pixel.blue() > pixel.red()                           # dark navy, not pure black


def test_incoming_previews_are_decoded_with_bounds(qapp):
    assert not decode_incoming_preview(jpeg((120, 80))).isNull()
    assert decode_incoming_preview(b"").isNull()
    assert decode_incoming_preview(b"\xff\xd8\xff garbage").isNull()
    assert decode_incoming_preview(jpeg((2000, 10))).isNull()   # absurd dimensions
    assert decode_incoming_preview(b"\xff\xd8\xff" + b"0" * (MAX_THUMBNAIL_BYTES + 1)).isNull()


# ---------------- the wire format
def test_encode_and_decode_roundtrip():
    data = jpeg((64, 48))
    assert decode_thumbnail(encode_thumbnail(data)) == data
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    assert decode_thumbnail(encode_thumbnail(png)) == png


@pytest.mark.parametrize("value", [None, 5, "", "!!!not base64!!!", "AAAA", "x" * 40000,
                                   [], {}, b"bytes"])
def test_bad_thumbnails_from_the_network_become_nothing(value):
    assert decode_thumbnail(value) == b""


def test_wrong_type_or_oversized_data_is_not_encoded():
    assert encode_thumbnail(None) is None and encode_thumbnail(b"") is None
    assert encode_thumbnail(b"GIF89a....") is None              # only JPEG / PNG
    assert encode_thumbnail(b"\xff\xd8\xff" + b"0" * MAX_THUMBNAIL_BYTES) is None


def test_a_bad_preview_never_rejects_the_offer(tmp_path):
    from aircatch.transfer.receiver import Receiver

    async def decide(offer):
        return True
    receiver = Receiver(tmp_path, decide)
    base = {"type": "offer", "version": 1, "transfer_id": "abc", "sender_name": "S",
            "filename": "f.txt", "size": 3}
    good, _ = receiver._parse_offer({**base, "thumbnail": encode_thumbnail(jpeg((32, 32)))}, "p")
    assert good is not None and good.thumbnail.startswith(b"\xff\xd8\xff")
    bad, error = receiver._parse_offer({**base, "thumbnail": "not-base64!!"}, "p")
    assert bad is not None and bad.thumbnail == b"" and error == ""
    none, _ = receiver._parse_offer(base, "p")
    assert none is not None and none.thumbnail == b""

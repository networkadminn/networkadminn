"""Linux capture helpers (mostly-black detection)."""

from timetrack.platform.linux_screenshot import _image_mostly_black


def test_mostly_black_detects_dark_frame():
    from PIL import Image

    img = Image.new("RGB", (100, 100), (2, 2, 2))
    assert _image_mostly_black(img)


def test_mostly_black_accepts_normal_frame():
    from PIL import Image

    img = Image.new("RGB", (100, 100), (120, 130, 140))
    assert not _image_mostly_black(img)

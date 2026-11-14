"""Make the application icon from the logo: the paper plane on the app's dark background.

  python packaging/make_icon.py

Writes packaging/aircatch.ico (for the .exe and the installer) and
src/aircatch/ui/assets/aircatch-icon.png (for the window and the taskbar). Needs Pillow.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "src" / "aircatch" / "ui" / "assets" / "aircatch-logo.png"
PLANE_BOX = (62, 18, 312, 212)          # where the paper plane sits in the logo
BACKGROUND = (7, 16, 31, 255)           # the app's dark blue
SIZE = 1024


def build() -> Image.Image:
    plane = Image.open(LOGO).convert("RGBA").crop(PLANE_BOX)
    # the white "A" of the lettering starts just right of the plane's tip: rub that fragment out
    pixels = plane.load()
    for x in range(int(plane.width * 0.80), plane.width):
        for y in range(int(plane.height * 0.36), plane.height):
            r, g, b, a = pixels[x, y]
            if a and min(r, g, b) > 200:
                pixels[x, y] = (r, g, b, 0)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=int(SIZE * 0.22), fill=255)
    canvas.paste(Image.new("RGBA", (SIZE, SIZE), BACKGROUND), (0, 0), mask)
    room = int(SIZE * 0.74)
    scale = min(room / plane.width, room / plane.height)
    plane = plane.resize((round(plane.width * scale), round(plane.height * scale)), Image.LANCZOS)
    canvas.alpha_composite(plane, ((SIZE - plane.width) // 2, (SIZE - plane.height) // 2))
    return canvas


def main() -> None:
    icon = build()
    icon.save(ROOT / "packaging" / "aircatch.ico",
              sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    icon.resize((256, 256), Image.LANCZOS).save(LOGO.with_name("aircatch-icon.png"))
    print("wrote packaging/aircatch.ico and src/aircatch/ui/assets/aircatch-icon.png")


if __name__ == "__main__":
    main()

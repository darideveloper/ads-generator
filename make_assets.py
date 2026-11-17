#!/usr/bin/env python3
"""Generate dummy brand assets so the pipeline runs before real photos exist."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "brands" / "example" / "assets"
BOLD = "/usr/share/fonts/truetype/litefonts/Roboto-Bold.ttf"
BLACK = (4, 18, 26, 255)
ACCENT = (34, 211, 238, 255)


def logo() -> None:
    size = 800
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=140, fill=BLACK)
    font = ImageFont.truetype(BOLD, 210)
    draw.text((size / 2, size / 2), "ACME", font=font, fill=ACCENT, anchor="mm")
    img.save(ASSETS / "logo.png")


def product() -> None:
    size = 1400
    img = Image.new("RGB", (size, size), (238, 240, 243))
    draw = ImageDraw.Draw(img)
    draw.ellipse((170, 1080, 1230, 1270), fill=(214, 218, 224))
    draw.rounded_rectangle((420, 320, 980, 1120), radius=90, fill=(28, 34, 44))
    draw.rounded_rectangle((470, 390, 930, 700), radius=50, fill=(52, 62, 76))
    draw.ellipse((560, 760, 840, 1040), fill=(16, 20, 28))
    draw.ellipse((630, 830, 770, 970), fill=(90, 100, 116))
    draw.rounded_rectangle((790, 470, 870, 610), radius=18, fill=ACCENT)
    draw.rounded_rectangle((560, 1120, 840, 1170), radius=24, fill=(12, 16, 22))
    img.save(ASSETS / "product-1.png")


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo()
    product()
    print(f"wrote {ASSETS / 'logo.png'} and {ASSETS / 'product-1.png'}")

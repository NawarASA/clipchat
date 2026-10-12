"""Draws the ClipChat app icon (build/icon.png + build/icon.ico). Run with the backend venv + Pillow."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024 * 4  # draw large, downscale for smooth edges
OUT = Path(__file__).resolve().parent.parent / "build"


def gradient(size, c0, c1):
    """Diagonal gradient from c0 (top-left) to c1 (bottom-right)."""
    v = Image.linear_gradient("L")
    g = ImageChops.add(v, v.transpose(Image.Transpose.ROTATE_90).transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                       scale=2.0).resize((size, size), Image.BICUBIC)
    return Image.composite(Image.new("RGB", (size, size), c1), Image.new("RGB", (size, size), c0), g)


def rounded(size, box, r):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, r, fill=255)
    return m


bg = gradient(S, (122, 92, 255), (255, 116, 176))
pad = int(S * 0.06)
tile = rounded(S, (pad, pad, S - pad, S - pad), int(S * 0.23))

# Soft drop shadow under the tile.
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
shadow.putalpha(tile.point(lambda v: int(v * 0.35)).filter(ImageFilter.GaussianBlur(S * 0.02)))
shadow = ImageChops.offset(shadow, 0, int(S * 0.015))

icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.alpha_composite(shadow)
icon.paste(bg, (0, 0), tile)

# White speech bubble with a tail, holding a gradient play triangle.
d = ImageDraw.Draw(icon)
bx0, by0, bx1, by1 = int(S * 0.24), int(S * 0.25), int(S * 0.76), int(S * 0.68)
d.rounded_rectangle((bx0, by0, bx1, by1), int(S * 0.12), fill=(255, 255, 255, 255))
d.polygon([(int(S * 0.33), by1 - int(S * 0.04)), (int(S * 0.29), int(S * 0.79)), (int(S * 0.46), by1 - int(S * 0.02))],
          fill=(255, 255, 255, 255))
cx, cy, r = (bx0 + bx1) / 2 + S * 0.015, (by0 + by1) / 2, S * 0.105
tri = Image.new("L", (S, S), 0)
ImageDraw.Draw(tri).polygon([(cx - r * 0.75, cy - r), (cx - r * 0.75, cy + r), (cx + r * 1.05, cy)], fill=255)
tri = tri.filter(ImageFilter.GaussianBlur(S * 0.0012))  # just takes the edge off the corners
icon.paste(gradient(S, (122, 92, 255), (230, 100, 200)), (0, 0), tri)

OUT.mkdir(exist_ok=True)
final = icon.resize((1024, 1024), Image.LANCZOS)
final.save(OUT / "icon.png")
final.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", OUT / "icon.png", OUT / "icon.ico")

"""Generate assets/hito.ico: a purple gradient tile with a donut-chart mark. Needs Pillow."""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "hito.ico")


def tile(size: int) -> Image.Image:
    s = size * 4  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    a, b = (124, 58, 237), (192, 38, 211)
    px = grad.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    c, r, w = s / 2, s * 0.31, s * 0.12
    box = (c - r, c - r, c + r, c + r)
    d.arc(box, -84, 168, fill=(255, 255, 255, 255), width=int(w))
    d.arc(box, 180, 262, fill=(221, 196, 255, 255), width=int(w))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    big = tile(256)
    big.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(OUT.replace(".ico", ".png"))
    print("wrote", OUT)

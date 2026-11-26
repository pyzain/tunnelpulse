"""Renders TunnelPulse's icon and installer artwork into assets/.

Needs Pillow (pip install pillow). Run from the repo root:
    python assets/make_art.py assets
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
S = 1024
ACCENT = (92, 127, 255)       # app dark-mode accent #5C7FFF
ACCENT_HI = (150, 175, 255)
TOP, BOTTOM = (28, 36, 72), (15, 18, 22)   # navy -> app background #0F1216

def gradient(w, h, top, bottom):
    g = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return g

def pulse_points(x0, x1, yc, amp):
    w = x1 - x0
    rel = [(0.00, 0), (0.30, 0), (0.38, -0.18), (0.44, 0.10), (0.52, -1.0),
           (0.60, 0.62), (0.67, 0), (1.00, 0)]
    return [(x0 + w * fx, yc + amp * fy) for fx, fy in rel]

def draw_mark(size, rounded=True, tile=True):
    """The app mark: a pulse line running through a tunnel of rings."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile_img = gradient(size, size, TOP, BOTTOM).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    r = int(size * 0.225) if rounded else 0
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    if tile:
        img.paste(tile_img, (0, 0), mask)
    else:
        mask = Image.new("L", (size, size), 255)

    rings = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rings)
    cx, cy = size / 2, size * 0.52
    for k, (rad, alpha) in enumerate([(0.40, 46), (0.29, 70), (0.18, 105)]):
        R = size * rad
        rd.ellipse([cx - R, cy - R, cx + R, cy + R], outline=ACCENT + (alpha,), width=max(2, int(size * 0.018)))
    img = Image.alpha_composite(img, Image.composite(rings, Image.new("RGBA", (size, size)), mask))

    pts = pulse_points(size * 0.12, size * 0.88, size * 0.52, size * 0.30)
    width = max(3, int(size * 0.062))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=ACCENT + (200,), width=int(width * 2.2), joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.035))
    img = Image.alpha_composite(img, Image.composite(glow, Image.new("RGBA", (size, size)), mask))
    line = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line)
    ld.line(pts, fill=ACCENT_HI + (255,), width=width, joint="curve")
    for p in (pts[0], pts[-1]):   # round the line ends
        ld.ellipse([p[0] - width / 2, p[1] - width / 2, p[0] + width / 2, p[1] + width / 2], fill=ACCENT_HI + (255,))
    img = Image.alpha_composite(img, line)
    return img

mark = draw_mark(S)
mark.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png", optimize=True)
ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
mark.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(s, s) for s in ico_sizes])
mark.resize((110, 110), Image.LANCZOS).save(OUT / "wizard-small.png", optimize=True)

# Left panel on the wizard's first and last page (164x314 at 100% scale; drawn at 2x).
W, H = 328, 628
panel = gradient(W, H, TOP, BOTTOM).convert("RGBA")
rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
rd = ImageDraw.Draw(rings)
cx, cy = W / 2, H * 0.42
for rad, alpha in [(0.95, 22), (0.72, 34), (0.50, 52), (0.30, 80)]:
    R = W * rad
    rd.ellipse([cx - R, cy - R, cx + R, cy + R], outline=ACCENT + (alpha,), width=3)
panel = Image.alpha_composite(panel, rings)
glyph = draw_mark(176, tile=False)
panel.alpha_composite(glyph, (int(cx - 88), int(cy - 88)))
panel.convert("RGB").save(OUT / "wizard.png", optimize=True)
for f in sorted(OUT.iterdir()):
    print(f.name, f.stat().st_size, "bytes")

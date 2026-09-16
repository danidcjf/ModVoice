"""
Generates the application icon, with no third-party dependency.

Pillow is not part of the build environment, so the PNG and ICO byte streams are
assembled here directly: an ICO file is a small header plus one embedded PNG per
size, and a PNG is just zlib-compressed scanlines.

Run:
    python tools/make_icon.py

Writes assets/app.ico and (to eyeball the result) build/icon_preview.png.
"""

import os
import struct
import zlib

import numpy as np

# Brand tokens, kept in sync with src/theme.py
BRAND = (0x25, 0xF4, 0xEE)
INK = (0x0F, 0x0F, 0x12)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

# A level-meter waveform: symmetric, tallest in the middle. Each entry is a
# fraction of the available inner height.
BAR_HEIGHTS = (0.45, 0.78, 1.0, 0.78, 0.45)
# Below 32 px five bars turn into mush, so small sizes use a simplified mark.
SMALL_BAR_HEIGHTS = (0.5, 1.0, 0.62)
SMALL_BAR_LIMIT = 32

BADGE_MARGIN = 0.055     # fraction of the canvas left empty around the badge
BADGE_RADIUS = 0.235     # corner radius, fraction of the badge size
INNER_RATIO = 0.56       # how much of the badge the bars may occupy
GAP_RATIO = 0.55         # gap between bars, as a fraction of a bar's width


def _rounded_rect(width: float, height: float, radius: float) -> np.ndarray:
    """Boolean (height, width) mask of a centred rounded rectangle."""
    radius = min(radius, width / 2.0, height / 2.0)
    x = np.arange(width, dtype=np.float64) + 0.5 - width / 2.0
    y = np.arange(height, dtype=np.float64) + 0.5 - height / 2.0
    dx = np.maximum(np.abs(x)[None, :] - (width / 2.0 - radius), 0.0)
    dy = np.maximum(np.abs(y)[:, None] - (height / 2.0 - radius), 0.0)
    return np.sqrt(dx ** 2 + dy ** 2) <= radius


def _paint(rgba: np.ndarray, mask: np.ndarray, colour) -> None:
    """Sets one RGBA colour wherever the mask is True."""
    rgba[..., 0][mask] = colour[0]
    rgba[..., 1][mask] = colour[1]
    rgba[..., 2][mask] = colour[2]
    rgba[..., 3][mask] = 255


def render(size: int) -> np.ndarray:
    """Renders the icon at `size` px as an (size, size, 4) uint8 RGBA array."""
    s = int(size * SUPERSAMPLE)
    canvas = np.zeros((s, s, 4), dtype=np.uint8)

    # 1. Rounded cyan badge filling the canvas
    margin = s * BADGE_MARGIN
    badge_size = s - 2 * margin
    badge_px = int(round(badge_size))
    pad = int(round(margin))
    badge = _rounded_rect(badge_size, badge_size, badge_size * BADGE_RADIUS)

    place = canvas[pad:pad + badge_px, pad:pad + badge_px]
    _paint(place, badge[:badge_px, :badge_px], BRAND)

    # 2. Bars forming a level meter, drawn in ink on top of the badge
    heights = SMALL_BAR_HEIGHTS if size < SMALL_BAR_LIMIT else BAR_HEIGHTS
    inner = badge_size * INNER_RATIO
    n = len(heights)
    bar_w = inner / (n + (n - 1) * GAP_RATIO)
    gap = bar_w * GAP_RATIO
    radius = bar_w / 2.0

    total_w = n * bar_w + (n - 1) * gap
    x_cursor = (badge_size - total_w) / 2.0
    centre = badge_size / 2.0

    for ratio in heights:
        bar_h = max(inner * ratio, bar_w)
        bw = max(int(round(bar_w)), 1)
        bh = max(int(round(bar_h)), 1)
        x0 = int(round(x_cursor))
        y0 = int(round(centre - bar_h / 2.0))

        bar = _rounded_rect(bar_w, bar_h, radius)
        region = place[y0:y0 + bh, x0:x0 + bw]
        if region.shape[:2] == (bh, bw):
            _paint(region, bar[:bh, :bw], INK)

        x_cursor += bar_w + gap

    # 3. Box-downsample the supersampled render for clean edges
    return canvas.reshape(size, SUPERSAMPLE, size, SUPERSAMPLE, 4).mean(axis=(1, 3))


def png_bytes(rgba: np.ndarray) -> bytes:
    """Minimal PNG encoder (8-bit RGBA, no interlacing, filter type 0)."""
    height, width, _ = rgba.shape
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y].tobytes())

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def ico_bytes(images: dict) -> bytes:
    """Assembles an ICO from {size: png bytes} using PNG-compressed entries."""
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)

    entries = bytearray()
    payload = bytearray()
    for size in sizes:
        data = images[size]
        entries.extend(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0, 0, 1, 32, len(data), offset,
        ))
        payload.extend(data)
        offset += len(data)

    return header + bytes(entries) + bytes(payload)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    assets = os.path.join(root, "assets")
    os.makedirs(assets, exist_ok=True)
    ico_path = os.path.join(assets, "app.ico")
    with open(ico_path, "wb") as f:
        f.write(ico_bytes({size: png_bytes(render(size)) for size in ICO_SIZES}))
    print(f"wrote {ico_path} ({os.path.getsize(ico_path)} bytes, sizes {list(ICO_SIZES)})")

    # Preview sheet: every size side by side, composited over grey so the
    # semi-transparent edges are judged the way they will actually look.
    slot, pad = 280, 16
    sheet = np.full((slot + 2 * pad, slot * len(ICO_SIZES) + 2 * pad, 4),
                    (0x77, 0x77, 0x7A, 255), dtype=np.uint8)
    for index, size in enumerate(ICO_SIZES):
        rgba = render(size)
        scale = max(1, 256 // size)
        if scale > 1:
            rgba = np.repeat(np.repeat(rgba, scale, axis=0), scale, axis=1)
        h, w, _ = rgba.shape
        x = pad + index * slot + (slot - w) // 2
        y = pad + (slot - h) // 2
        alpha = rgba[..., 3:4].astype(np.float32) / 255.0
        dest = sheet[y:y + h, x:x + w, :3].astype(np.float32)
        sheet[y:y + h, x:x + w, :3] = (
            rgba[..., :3].astype(np.float32) * alpha + dest * (1.0 - alpha)
        ).astype(np.uint8)

    build = os.path.join(root, "build")
    os.makedirs(build, exist_ok=True)
    preview = os.path.join(build, "icon_preview.png")
    with open(preview, "wb") as f:
        f.write(png_bytes(sheet))
    print(f"wrote {preview}")


if __name__ == "__main__":
    main()

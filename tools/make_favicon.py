"""Render `public/favicon.ico` from `public/aira-mark.svg`.

The tab icon was Angular's default, unchanged since the Phase 0 shell — the AIRA mark was in the
page header and nowhere else, so every browser tab of the governance console carried a framework's
logo. Reported by the owner, who is the only person who could have: a favicon is invisible to every
test that checks behaviour, and looking right is the whole of its job.

**Generated rather than drawn.** A second hand-made image is a second definition of one mark, and
this repository has paid for that shape often enough to know how it ends: the SVG gets a new colour
and the `.ico` keeps the old one, in the place nobody looks. So this reads the mark's own geometry
and rasterises it; `tools/tests/test_favicon_is_the_aira_mark.py` regenerates and compares, which
fails when the SVG changes and the icon does not.

It understands the subset the mark uses — `rect`, `line`, `circle`, one linear gradient — and no
more. A primitive it does not know is an error rather than a silent omission, because a mark that
renders as three quarters of itself still looks like an icon.

PNG and ICO are written by hand: no imaging library is installed here, and adding one so that a
2 kB file can be produced would be the larger change.

    uv run python tools/make_favicon.py --write
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "management" / "frontend" / "public"
MARK = PUBLIC / "aira-mark.svg"
ICON = PUBLIC / "favicon.ico"

#: The sizes a browser actually asks for. 16 is the tab, 32 the bookmark bar and the retina tab.
SIZES = (16, 32, 48)
#: Supersampling factor. Enough that a 6-unit stroke at 16 px keeps a clean edge.
OVERSAMPLE = 8

Colour = tuple[int, int, int]


def _hex(value: str) -> Colour:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _attrs(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag))


def _translate(source: str) -> tuple[float, float]:
    match = re.search(r'transform="translate\(([-\d.]+)[ ,]+([-\d.]+)\)"', source)
    return (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)


def _gradient(source: str) -> tuple[Colour, Colour]:
    stops = re.findall(r'<stop[^>]*stop-color="([^"]+)"', source)
    if len(stops) < 2:
        raise SystemExit("aira-mark.svg: expected a two-stop gradient")
    return _hex(stops[0]), _hex(stops[1])


def _shapes(source: str) -> list[tuple[str, dict[str, str]]]:
    """The mark's primitives, in document order, with their inherited stroke settings."""
    shapes: list[tuple[str, dict[str, str]]] = []
    group: dict[str, str] = {}
    body = source[source.index("<g transform=") :]
    for tag in re.findall(r"<(/?g[^>]*|rect[^>]*|line[^>]*|circle[^>]*)>", body):
        name = tag.split()[0].lstrip("/")
        if tag.startswith("/g"):
            group = {}
            continue
        if name == "g":
            group = _attrs(tag)
            continue
        shapes.append((name, {**group, **_attrs(tag)}))
    return shapes


def _render(size: int, source: str) -> bytes:
    """RGBA rows for one square icon, drawn at `OVERSAMPLE`× and averaged down."""
    view = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', source)
    if not view:
        raise SystemExit("aira-mark.svg: no viewBox")
    width, height = float(view.group(1)), float(view.group(2))
    span = max(width, height)
    big = size * OVERSAMPLE
    scale = big / span
    # Centred, so a non-square mark keeps its proportions instead of being stretched to the box.
    pad_x = (span - width) / 2 * scale
    pad_y = (span - height) / 2 * scale

    start, end = _gradient(source)
    dx, dy = _translate(source)
    canvas = [[(0, 0, 0, 0)] * big for _ in range(big)]

    def put(px: int, py: int, colour: Colour) -> None:
        if 0 <= px < big and 0 <= py < big:
            canvas[py][px] = (*colour, 255)

    def gradient_at(x: float, y: float) -> Colour:
        t = min(1.0, max(0.0, (x / width + y / height) / 2))
        return tuple(round(a + (b - a) * t) for a, b in zip(start, end, strict=True))  # type: ignore[return-value]

    def fill_rect(x: float, y: float, w: float, h: float, colour: str) -> None:
        for py in range(int((y + dy) * scale + pad_y), int((y + dy + h) * scale + pad_y)):
            for px in range(int((x + dx) * scale + pad_x), int((x + dx + w) * scale + pad_x)):
                shade = (
                    gradient_at(x + (px - pad_x) / scale, y)
                    if colour.startswith("url")
                    else _hex(colour)
                )
                put(px, py, shade)

    def stroke_line(x1: float, y1: float, x2: float, y2: float, w: float, colour: str) -> None:
        half = w / 2
        fill_rect(
            min(x1, x2) - half, min(y1, y2) - half, abs(x2 - x1) + w, abs(y2 - y1) + w, colour
        )

    def fill_circle(cx: float, cy: float, r: float, colour: str) -> None:
        shade = _hex(colour)
        centre_x, centre_y = (cx + dx) * scale + pad_x, (cy + dy) * scale + pad_y
        radius = r * scale
        for py in range(int(centre_y - radius), int(centre_y + radius) + 1):
            for px in range(int(centre_x - radius), int(centre_x + radius) + 1):
                if (px + 0.5 - centre_x) ** 2 + (py + 0.5 - centre_y) ** 2 <= radius**2:
                    put(px, py, shade)

    for name, a in _shapes(source):
        if name == "rect":
            fill_rect(
                float(a["x"]), float(a["y"]), float(a["width"]), float(a["height"]), a["fill"]
            )
        elif name == "line":
            stroke_line(
                float(a["x1"]),
                float(a["y1"]),
                float(a["x2"]),
                float(a["y2"]),
                float(a.get("stroke-width", "1")),
                a["stroke"],
            )
        elif name == "circle":
            fill_circle(float(a["cx"]), float(a["cy"]), float(a["r"]), a["fill"])
        else:  # pragma: no cover — the mark uses three primitives and nothing else
            raise SystemExit(f"aira-mark.svg uses <{name}>, which this renderer does not know")

    rows = bytearray()
    step = OVERSAMPLE
    for y in range(size):
        rows.append(0)  # PNG filter: none
        for x in range(size):
            samples = [
                canvas[y * step + oy][x * step + ox] for oy in range(step) for ox in range(step)
            ]
            alpha = sum(s[3] for s in samples) / len(samples)
            if alpha == 0:
                rows.extend((0, 0, 0, 0))
                continue
            # Averaged over the covered samples only, so an edge blends with the mark's colour
            # rather than with black.
            covered = [s for s in samples if s[3]]
            rows.extend(
                (
                    round(sum(s[0] for s in covered) / len(covered)),
                    round(sum(s[1] for s in covered) / len(covered)),
                    round(sum(s[2] for s in covered) / len(covered)),
                    round(alpha),
                )
            )
    return bytes(rows)


def _png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build() -> bytes:
    """An ICO carrying one PNG per size — the modern form, understood everywhere it matters."""
    source = MARK.read_text()
    images = [_png(size, _render(size, source)) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, image in zip(SIZES, images, strict=True):
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset + len(payload)
        )
        payload += image
    return header + entries + payload


def main(argv: list[str]) -> int:
    data = build()
    if "--write" in argv:
        ICON.write_bytes(data)
        print(f"wrote {ICON.relative_to(ROOT)} ({len(data)} bytes)")
    else:
        print(f"{len(data)} bytes; pass --write to update {ICON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))

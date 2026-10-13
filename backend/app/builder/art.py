"""Deterministic, on-brand artwork as real SVG files.

A generated project must never reference an image that does not exist, and must never pass off a stock photo as the
owner's product. So every card, hero and detail page without an owner-supplied picture gets a designed SVG written
into the project itself (`assets/art/*.svg`). An owner-supplied https image replaces it by editing the data file.
"""

from __future__ import annotations

import hashlib
from html import escape

from app.builder.design import _mix
from app.builder.spec import Palette


def _h(seed: str) -> list[int]:
    return list(hashlib.sha256(seed.encode("utf-8")).digest())


def art_svg(seed: str, palette: Palette, label: str = "") -> str:
    b = _h(seed)
    a1 = palette.primary
    a2 = _mix(palette.primary, palette.secondary, 0.55 + (b[0] % 40) / 100)
    glow = palette.accent
    angle = b[1] % 360
    circles = []
    for i in range(3):
        cx, cy = 80 + (b[2 + i * 3] % 640), 60 + (b[3 + i * 3] % 480)
        r = 90 + (b[4 + i * 3] % 190)
        op = 0.10 + (b[5 + i] % 14) / 100
        circles.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#ffffff" fill-opacity="{op:.2f}"/>')
    bars = []
    for i in range(5):
        x = 70 + i * 135
        h = 80 + (b[14 + i] % 220)
        bars.append(f'<rect x="{x}" y="{480 - h}" width="64" height="{h}" rx="10" fill="#ffffff" fill-opacity="0.12"/>')
    glyph = escape((label.strip()[:1] or "•").upper())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" role="img" aria-label="' + escape(label or "Illustration") + '" preserveAspectRatio="xMidYMid slice">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle} .5 .5)"><stop offset="0" stop-color="{a1}"/><stop offset="1" stop-color="{a2}"/></linearGradient>'
        f'<radialGradient id="r" cx=".85" cy=".1" r=".7"><stop offset="0" stop-color="{glow}" stop-opacity=".55"/><stop offset="1" stop-color="{glow}" stop-opacity="0"/></radialGradient></defs>'
        '<rect width="800" height="600" fill="url(#g)"/><rect width="800" height="600" fill="url(#r)"/>'
        + "".join(circles)
        + "".join(bars)
        + f'<text x="400" y="340" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif" font-size="190" font-weight="700" fill="#ffffff" fill-opacity="0.92">{glyph}</text></svg>'
    )

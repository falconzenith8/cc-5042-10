"""RGBA colour helpers and straight-alpha source-over blending."""
from __future__ import annotations


def rgba(r: int, g: int, b: int, a: int = 255) -> tuple[int, int, int, int]:
    return (r & 255, g & 255, b & 255, a & 255)


def with_alpha(color, a: int):
    return (color[0], color[1], color[2], a & 255)


def blend(dst: tuple[int, int, int, int],
          src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Source-over for straight (non-premultiplied) 8-bit channels."""
    sr, sg, sb, sa = src
    if sa == 255:
        return (sr, sg, sb, 255)
    if sa == 0:
        return dst
    dr, dg, db, da = dst
    inv = 255 - sa
    out_a = sa + (da * inv + 127) // 255
    if out_a == 0:
        return (0, 0, 0, 0)
    r = (sr * sa + dr * da * inv // 255) // out_a
    g = (sg * sa + dg * da * inv // 255) // out_a
    b = (sb * sa + db * da * inv // 255) // out_a
    return (r, g, b, out_a)

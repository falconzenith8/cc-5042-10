"""Integer/device-space rectangles and affine view transform.

The whole pipeline keeps explicit device-integer rectangles.  Bounds are
always expanded *outward* so strokes and blurs never bleed outside a
computed invalid region.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """Axis aligned rectangle in device pixels, half-open: [x0, x1)."""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self):
        object.__setattr__(self, "x0", int(self.x0))
        object.__setattr__(self, "y0", int(self.y0))
        object.__setattr__(self, "x1", int(self.x1))
        object.__setattr__(self, "y1", int(self.y1))

    @staticmethod
    def from_ltrb(x0, y0, x1, y1) -> "Rect":
        return Rect(int(math.floor(x0)), int(math.floor(y0)),
                    int(math.ceil(x1)), int(math.ceil(y1)))

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        w, h = self.width, self.height
        return w * h if w > 0 and h > 0 else 0

    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def union(self, other: "Rect") -> "Rect":
        if self.is_empty():
            return other
        if other.is_empty():
            return self
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0),
                    max(self.x1, other.x1), max(self.y1, other.y1))

    def intersect(self, other: "Rect") -> "Rect":
        r = Rect(max(self.x0, other.x0), max(self.y0, other.y0),
                 min(self.x1, other.x1), min(self.y1, other.y1))
        return r if not r.is_empty() else Rect(0, 0, 0, 0)

    def intersects(self, other: "Rect") -> bool:
        return not (self.x1 <= other.x0 or other.x1 <= self.x0 or
                    self.y1 <= other.y0 or other.y1 <= self.y0)

    def inflated(self, dx: int, dy: int | None = None) -> "Rect":
        if dy is None:
            dy = dx
        return Rect(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)

    def shifted(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def clamp_to(self, bounds: "Rect") -> "Rect":
        return self.intersect(bounds)

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))


@dataclass(frozen=True)
class Transform:
    """device = logical * scale + offset.  Offset is in device pixels."""

    scale: float
    ox: float
    oy: float

    def to_device_x(self, x: float) -> float:
        return x * self.scale + self.ox

    def to_device_y(self, y: float) -> float:
        return y * self.scale + self.oy

    def to_logical_x(self, x: float) -> float:
        return (x - self.ox) / self.scale

    def to_logical_y(self, y: float) -> float:
        return (y - self.oy) / self.scale

    def logical_rect_to_device(self, x0, y0, x1, y1) -> Rect:
        return Rect.from_ltrb(self.to_device_x(x0), self.to_device_y(y0),
                              self.to_device_x(x1), self.to_device_y(y1))

    def device_delta(self, logical_d: float) -> float:
        return logical_d * self.scale


def merge_rects(rects, viewport: Rect) -> list[Rect]:
    """Greedily union overlapping/adjacent rects, clipped to the viewport.

    Keeps disjoint rects separate so a tiny edit does not repaint the whole
    frame; unions only when rectangles actually touch, which avoids seams
    when replaying the paint order across neighbouring dirty boxes.
    """
    clipped: list[Rect] = []
    for r in rects:
        r = r.clamp_to(viewport)
        if not r.is_empty():
            clipped.append(r)
    merged = True
    while merged:
        merged = False
        out: list[Rect] = []
        for r in clipped:
            hit = -1
            for i, m in enumerate(out):
                if r.intersects(m):
                    hit = i
                    break
            if hit >= 0:
                out[hit] = out[hit].union(r)
                merged = True
            else:
                out.append(r)
        clipped = out
    return clipped

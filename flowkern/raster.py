"""Dependency-free RGBA software rasteriser.

Everything is rasterised in straight 8-bit alpha with an explicit clip
stack.  The surface also counts the real work (pixels touched, blits,
blended pixels, scroll-copied pixels) so the kernel can compare partial
repaint against a full-frame reference instead of guessing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Rect
from . import font as bmfont


@dataclass
class WorkStats:
    commands: int = 0
    fast_fill_px: int = 0
    blend_px: int = 0
    text_px: int = 0
    blit_px: int = 0
    shadow_px: int = 0
    scroll_px: int = 0
    clip_pushes: int = 0

    @property
    def painted_px(self) -> int:
        return (self.fast_fill_px + self.blend_px + self.text_px
                + self.blit_px + self.shadow_px)

    def as_dict(self) -> dict:
        return {
            "commands": self.commands,
            "fast_fill_px": self.fast_fill_px,
            "blend_px": self.blend_px,
            "text_px": self.text_px,
            "blit_px": self.blit_px,
            "shadow_px": self.shadow_px,
            "scroll_px": self.scroll_px,
            "clip_pushes": self.clip_pushes,
            "painted_px": self.painted_px,
        }


class Surface:
    def __init__(self, width: int, height: int,
                 bg=(245, 246, 250, 255)):
        self.w = int(width)
        self.h = int(height)
        self.bg = tuple(bg)
        self.buf = bytearray(self.w * self.h * 4)
        self.clear(bg)
        self._clip_stack: list[Rect] = [Rect(0, 0, self.w, self.h)]
        self.stats = WorkStats()

    # ------------------------------------------------------------------ clip
    @property
    def clip(self) -> Rect:
        return self._clip_stack[-1]

    def push_clip(self, rect: Rect) -> Rect:
        self.stats.clip_pushes += 1
        self._clip_stack.append(self.clip.intersect(rect))
        return self._clip_stack[-1]

    def pop_clip(self) -> None:
        if len(self._clip_stack) > 1:
            self._clip_stack.pop()

    def _clamped(self, x0, y0, x1, y1) -> Rect | None:
        r = Rect.from_ltrb(x0, y0, x1, y1).clamp_to(self.clip)
        return None if r.is_empty() else r

    # ----------------------------------------------------------------- clear
    def clear(self, color=None) -> None:
        color = self.bg if color is None else color
        r, g, b, a = color
        row = bytes((r, g, b, a)) * self.w
        start = 0
        for _ in range(self.h):
            self.buf[start:start + self.w * 4] = row
            start += self.w * 4

    def fill_bg_rect(self, rect: Rect) -> None:
        """Opaque background erase inside rect (clip respected)."""
        r = rect.clamp_to(self.clip)
        if r.is_empty():
            return
        r0, g0, b0, a0 = self.bg
        row = bytes((r0, g0, b0, a0)) * r.width
        off = (r.y0 * self.w + r.x0) * 4
        step = self.w * 4
        for _ in range(r.height):
            self.buf[off:off + r.width * 4] = row
            off += step
        self.stats.commands += 1
        self.stats.fast_fill_px += r.area

    # ------------------------------------------------------------- flat fill
    def fill_rect(self, x0, y0, x1, y1, color) -> None:
        self.stats.commands += 1
        r = self._clamped(x0, y0, x1, y1)
        if r is None:
            return
        if color[3] == 255:
            self._solid_span_rect(r, color)
        else:
            self._blend_span_rect(r, color)

    def _solid_span_rect(self, r: Rect, color) -> None:
        rr, gg, bb, aa = color
        row = bytes((rr, gg, bb, aa)) * r.width
        off = (r.y0 * self.w + r.x0) * 4
        step = self.w * 4
        for _ in range(r.height):
            self.buf[off:off + r.width * 4] = row
            off += step
        self.stats.fast_fill_px += r.area

    def _blend_span_rect(self, r: Rect, color) -> None:
        sr, sg, sb, sa = color
        inv = 255 - sa
        buf = self.buf
        w4 = self.w * 4
        n = 0
        for y in range(r.y0, r.y1):
            off = (y * self.w + r.x0) * 4
            for _ in range(r.x1 - r.x0):
                da = buf[off + 3]
                if da:
                    buf[off] = (sr * sa + buf[off] * da * inv // 255) // (
                        sa + (da * inv + 127) // 255)
                    buf[off + 1] = (sg * sa + buf[off + 1] * da * inv
                                    // 255) // (
                        sa + (da * inv + 127) // 255)
                    buf[off + 2] = (sb * sa + buf[off + 2] * da * inv
                                    // 255) // (
                        sa + (da * inv + 127) // 255)
                    buf[off + 3] = sa + (da * inv + 127) // 255
                else:
                    buf[off] = sr
                    buf[off + 1] = sg
                    buf[off + 2] = sb
                    buf[off + 3] = sa
                off += 4
                n += 1
        self.stats.blend_px += n

    def fill_spans(self, spans, color) -> None:
        """spans: iterable of (y, x0, x1) device coords."""
        self.stats.commands += 1
        solid = color[3] == 255
        for y, xa, xb in spans:
            r = self._clamped(xa, y, xb, y + 1)
            if r is None:
                continue
            if solid:
                self._solid_span_rect(r, color)
            else:
                self._blend_span_rect(r, color)

    # -------------------------------------------------------------- polygons
    def fill_polygon(self, points, color) -> None:
        self.stats.commands += 1
        spans = list(convex_polygon_spans(points, self.clip))
        if not spans:
            return
        if color[3] == 255:
            for y, xa, xb in spans:
                r = Rect(xa, y, xb, y + 1)
                self._solid_span_rect(r, color)
        else:
            for y, xa, xb in spans:
                r = Rect(xa, y, xb, y + 1)
                self._blend_span_rect(r, color)

    def stroke_line(self, x0, y0, x1, y1, width, color) -> None:
        quad = line_quad(x0, y0, x1, y1, width)
        self.fill_polygon(quad, color)

    # ---------------------------------------------------------- rounded rect
    def rounded_rect(self, x0, y0, x1, y1, radius, fill, stroke,
                     stroke_w: float) -> None:
        """Fill + ring in one pass so translucent fill never double-blends
        the stroke underneath it."""
        self.stats.commands += 1
        r = self._clamped(x0, y0, x1, y1)
        if r is None:
            return
        rad = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
        sw = max(0.0, min(stroke_w, (x1 - x0) / 2, (y1 - y0) / 2))
        for y in range(max(0, math.floor(y0)), min(self.h, math.ceil(y1))):
            if y < self.clip.y0 or y >= self.clip.y1:
                continue
            dy_c = y + 0.5
            ol, orr = _rounded_span(x0, y0, x1, y1, rad, dy_c)
            if ol is None:
                continue
            il = ir = None
            if sw > 0 and fill is not None:
                il, ir = _rounded_span(x0 + sw, y0 + sw, x1 - sw, y1 - sw,
                                       max(0.0, rad - sw), dy_c)
                if il is not None and ir <= il:
                    # Scan line crosses the outer corner but never reaches
                    # the inner rounded rect: ring only, no fill on this row.
                    il = ir = None
            if stroke is not None and sw > 0:
                self.fill_spans([(y, ol, il if il is not None else orr)],
                                stroke)
                if ir is not None:
                    self.fill_spans([(y, ir, orr)], stroke)
            if fill is not None:
                if il is not None:
                    self.fill_spans([(y, il, ir)], fill)
                elif stroke is None or sw == 0:
                    self.fill_spans([(y, ol, orr)], fill)

    def fill_ellipse(self, x0, y0, x1, y1, color) -> None:
        self.stats.commands += 1
        spans = list(ellipse_spans(x0, y0, x1, y1, self.clip))
        if not spans:
            return
        solid = color[3] == 255
        for y, xa, xb in spans:
            r = Rect(xa, y, xb, y + 1)
            if solid:
                self._solid_span_rect(r, color)
            else:
                self._blend_span_rect(r, color)

    # ------------------------------------------------------------- shapes
    def draw_shape(self, p: dict, color, stroke: bool,
                   stroke_w: float = 0.0) -> None:
        x0, y0, x1, y1 = p["x0"], p["y0"], p["x1"], p["y1"]
        shape = p["shape"]
        if shape == "diamond":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
            if stroke:
                pts = _diamond_points(cx, cy, hw, hh)
                self._stroke_polyline(pts + [pts[0]], stroke_w, color,
                                      closed=True)
            else:
                self.fill_polygon(_diamond_points(cx, cy, hw, hh), color)
        elif shape == "ellipse":
            if stroke:
                self._stroke_ellipse(x0, y0, x1, y1, stroke_w, color)
            else:
                self.fill_ellipse(x0, y0, x1, y1, color)
        else:
            rad = 0.0 if shape == "rect" else p.get("radius", 0.0)
            if stroke:
                self.rounded_rect(x0, y0, x1, y1, rad, None, color,
                                  stroke_w)
            else:
                self.rounded_rect(x0, y0, x1, y1, rad, color, None, 0.0)

    def draw_shadow(self, p: dict, color) -> None:
        x0, y0, x1, y1 = p["x0"], p["y0"], p["x1"], p["y1"]
        blur = max(0, int(p["blur"]))
        # The mask is always built in local mask space with the geometry at
        # (blur, blur); the composite origin places it in the target surface.
        ox, oy = p.get("_origin",
                       (math.floor(x0) - blur, math.floor(y0) - blur))
        mw = max(1, math.ceil(x1 - x0) + blur * 2)
        mh = max(1, math.ceil(y1 - y0) + blur * 2)
        mask = AlphaMask(mw, mh)
        lx0, ly0 = blur, blur
        lx1, ly1 = lx0 + (x1 - x0), ly0 + (y1 - y0)
        shape = p["shape"]
        if shape == "diamond":
            cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
            mask.fill_polygon(_diamond_points(
                cx, cy, (lx1 - lx0) / 2, (ly1 - ly0) / 2))
        elif shape == "ellipse":
            for y, xa, xb in ellipse_spans(lx0, ly0, lx1, ly1,
                                           Rect(0, 0, mw, mh)):
                mask.data[y * mw + xa:y * mw + xb] = b"\xff" * (xb - xa)
        else:
            mask.fill_rect(lx0, ly0, lx1, ly1)
        mask = mask.box_blur(blur)
        self.composite_mask(mask, ox, oy, color)

    def _stroke_ellipse(self, x0, y0, x1, y1, width, color) -> None:
        steps = max(24, int((x1 - x0 + y1 - y0) / 3))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
        pts = [(cx + rx * math.cos(2 * math.pi * i / steps),
                cy + ry * math.sin(2 * math.pi * i / steps))
               for i in range(steps)]
        self._stroke_polyline(pts + [pts[0]], width, color, closed=True)

    def _stroke_polyline(self, pts, width, color, closed: bool) -> None:
        n = len(pts)
        for i in range(n - 1):
            self.stroke_line(pts[i][0], pts[i][1], pts[i + 1][0],
                             pts[i + 1][1], width, color)

    # ------------------------------------------------------------------ text
    def draw_text(self, x, y, text: str, scale: float, color) -> None:
        self.stats.commands += 1
        solid = color[3] == 255
        # Floor the pen origin so rasterisation is translation-invariant:
        # glyphs land on the same device columns regardless of the surface
        # (frame vs cached object layer) they are drawn into.
        x = math.floor(x)
        y = math.floor(y)
        cy = y
        for line in text.split("\n"):
            cx = x
            for ch in line:
                rows = bmfont.glyph_rows(ch)
                for gy, row in enumerate(rows):
                    gx = 0
                    while gx < bmfont.GLYPH_W:
                        if row[gx] != "#":
                            gx += 1
                            continue
                        start = gx
                        while gx < bmfont.GLYPH_W and row[gx] == "#":
                            gx += 1
                        px0 = cx + start * scale
                        px1 = cx + gx * scale
                        py0 = cy + gy * scale
                        py1 = cy + (gy + 1) * scale
                        r = self._clamped(px0, py0, px1, py1)
                        if r is not None:
                            area = r.area
                            if solid:
                                self._solid_span_rect(r, color)
                            else:
                                self._blend_span_rect(r, color)
                            self.stats.text_px += area
                cx += bmfont.ADVANCE * scale
            cy += bmfont.LINE_H * scale

    # ------------------------------------------------------------------ blit
    def blit(self, src: "Surface", dx: int, dy: int, global_alpha: int = 255):
        self.stats.commands += 1
        # Floor (not round): rasterisers below floor sub-pixel origins, so
        # the layer placement must floor too or glyphs shift by one column.
        dx, dy = int(math.floor(dx)), int(math.floor(dy))
        # Intersect the source rectangle (placed at dx,dy) with the clip.
        sx0 = max(0, self.clip.x0 - dx)
        sy0 = max(0, self.clip.y0 - dy)
        sx1 = min(src.w, self.clip.x1 - dx)
        sy1 = min(src.h, self.clip.y1 - dy)
        if sx1 <= sx0 or sy1 <= sy0:
            return
        buf, sb = self.buf, src.buf
        w = self.w
        n = 0
        for yy in range(sy0, sy1):
            doff = ((yy + dy) * w + sx0 + dx) * 4
            soff = (yy * src.w + sx0) * 4
            for _ in range(sx1 - sx0):
                sa = sb[soff + 3]
                if sa:
                    if global_alpha != 255:
                        sa = sa * global_alpha // 255
                    if sa == 255:
                        buf[doff] = sb[soff]
                        buf[doff + 1] = sb[soff + 1]
                        buf[doff + 2] = sb[soff + 2]
                        buf[doff + 3] = 255
                    else:
                        inv = 255 - sa
                        da = buf[doff + 3]
                        out_a = sa + (da * inv + 127) // 255
                        if out_a:
                            buf[doff] = (sb[soff] * sa + buf[doff] * da
                                         * inv // 255) // out_a
                            buf[doff + 1] = (sb[soff + 1] * sa
                                             + buf[doff + 1] * da * inv
                                             // 255) // out_a
                            buf[doff + 2] = (sb[soff + 2] * sa
                                             + buf[doff + 2] * da * inv
                                             // 255) // out_a
                            buf[doff + 3] = out_a
                    n += 1
                soff += 4
                doff += 4
        self.stats.blit_px += n

    # ---------------------------------------------------------------- scroll
    def scroll(self, dx: int, dy: int, bg=None) -> Rect:
        """Move existing pixels; return the exposed rect that needs repaint.

        Only pixels that stay inside the viewport are copied; the revealed
        strip is filled with the background and reported separately.
        """
        bg = self.bg if bg is None else bg
        old = bytearray(self.buf)
        row_len = self.w * 4
        bg_row = bytes(bg) * self.w
        exposed: list[Rect] = []
        for y in range(self.h):
            sy = y - dy
            doff = y * row_len
            if 0 <= sy < self.h:
                soff = sy * row_len
                if dx == 0:
                    self.buf[doff:doff + row_len] = old[soff:soff + row_len]
                else:
                    xs0 = max(0, dx)
                    xs1 = min(self.w, self.w + dx)
                    if xs1 > xs0:
                        s = soff + (xs0 - dx) * 4
                        d = doff + xs0 * 4
                        length = (xs1 - xs0) * 4
                        self.buf[d:d + length] = old[s:s + length]
                    if dx > 0:
                        self.buf[doff:doff + dx * 4] = bg_row[:dx * 4]
                    elif dx < 0:
                        cut = self.w + dx
                        self.buf[doff + cut * 4:doff + row_len] = \
                            bg_row[:(-dx) * 4]
            else:
                self.buf[doff:doff + row_len] = bg_row
        if dy > 0:
            exposed.append(Rect(0, 0, self.w, dy))
        elif dy < 0:
            exposed.append(Rect(0, self.h + dy, self.w, self.h))
        if dx > 0:
            exposed.append(Rect(0, max(0, dy), dx, self.h - abs(dy)))
        elif dx < 0:
            exposed.append(Rect(self.w + dx, max(0, dy), self.w,
                                self.h - abs(dy)))
        kept = self.w * self.h - sum(r.area for r in exposed)
        self.stats.scroll_px += max(0, kept)
        self.stats.commands += 1
        if len(exposed) == 1:
            return exposed[0]
        out = Rect(0, 0, 0, 0)
        for r in exposed:
            out = out.union(r)
        return out

    # ---------------------------------------------------------- alpha masks
    def composite_mask(self, mask: "AlphaMask", ox: int, oy: int,
                       color) -> None:
        """Paint a blurred alpha mask with `color` (its alpha is ignored)."""
        self.stats.commands += 1
        buf, mb = self.buf, self.buf
        sr, sg, sb = color[0], color[1], color[2]
        ox, oy = int(math.floor(ox)), int(math.floor(oy))
        n = 0
        x0 = max(0, self.clip.x0 - ox)
        y0 = max(0, self.clip.y0 - oy)
        x1 = min(mask.w, self.clip.x1 - ox)
        y1 = min(mask.h, self.clip.y1 - oy)
        for yy in range(y0, y1):
            moff = yy * mask.w + x0
            doff = ((yy + oy) * self.w + x0 + ox) * 4
            for _ in range(x1 - x0):
                sa = mask.data[moff]
                if sa:
                    inv = 255 - sa
                    da = buf[doff + 3]
                    out_a = sa + (da * inv + 127) // 255
                    if out_a:
                        buf[doff] = (sr * sa + buf[doff] * da * inv
                                     // 255) // out_a
                        buf[doff + 1] = (sg * sa + buf[doff + 1] * da * inv
                                         // 255) // out_a
                        buf[doff + 2] = (sb * sa + buf[doff + 2] * da * inv
                                         // 255) // out_a
                        buf[doff + 3] = out_a
                    n += 1
                moff += 1
                doff += 4
        self.stats.shadow_px += n

    # ---------------------------------------------------------------- output
    def pixel(self, x: int, y: int):
        off = (y * self.w + x) * 4
        return tuple(self.buf[off:off + 4])

    def write_ppm(self, path: str) -> None:
        out = bytearray()
        out += f"P6\n{self.w} {self.h}\n255\n".encode()
        b = self.buf
        for i in range(0, len(b), 4):
            out += bytes((b[i], b[i + 1], b[i + 2]))
        with open(path, "wb") as f:
            f.write(out)

    def reset_stats(self) -> None:
        self.stats = WorkStats()


class AlphaMask:
    """Single-channel mask used for drop shadows."""

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height
        self.data = bytearray(width * height)

    def fill_rect(self, x0, y0, x1, y1) -> None:
        for y in range(max(0, math.floor(y0)), min(self.h, math.ceil(y1))):
            a = y * self.w + max(0, math.floor(x0))
            end = y * self.w + min(self.w, math.ceil(x1))
            if end > a:
                self.data[a:end] = b"\xff" * (end - a)

    def fill_polygon(self, points) -> None:
        clip = Rect(0, 0, self.w, self.h)
        for y, xa, xb in convex_polygon_spans(points, clip):
            a = y * self.w + xa
            self.data[a:a + (xb - xa)] = b"\xff" * (xb - xa)

    def box_blur(self, radius: int) -> "AlphaMask":
        radius = int(radius)
        if radius <= 0:
            return self
        tmp = bytearray(self.w * self.h)
        out = bytearray(self.w * self.h)
        src, w, h = self.data, self.w, self.h
        k = radius * 2 + 1
        for y in range(h):
            row = y * w
            acc = 0
            for x in range(-radius, radius + 1):
                acc += src[row + min(w - 1, max(0, x))]
            for x in range(w):
                tmp[row + x] = acc // k
                xr = x + radius + 1
                xl = x - radius
                acc += src[row + min(w - 1, max(0, xr))]
                acc -= src[row + min(w - 1, max(0, xl))]
        for x in range(w):
            acc = 0
            for y in range(-radius, radius + 1):
                acc += tmp[min(h - 1, max(0, y)) * w + x]
            for y in range(h):
                out[y * w + x] = acc // k
                yr = y + radius + 1
                yl = y - radius
                acc += tmp[min(h - 1, max(0, yr)) * w + x]
                acc -= tmp[min(h - 1, max(0, yl)) * w + x]
        res = AlphaMask(w, h)
        res.data = out
        return res


# --------------------------------------------------------------------- math
def _diamond_points(cx, cy, hw, hh):
    return [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]


def line_quad(x0, y0, x1, y1, width) -> list[tuple[float, float]]:
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        hw = width / 2
        return [(x0 - hw, y0 - hw), (x0 + hw, y0 - hw),
                (x0 + hw, y0 + hw), (x0 - hw, y0 + hw)]
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    hw = width / 2
    return [(x0 + nx * hw, y0 + ny * hw),
            (x0 - nx * hw, y0 - ny * hw),
            (x1 - nx * hw, y1 - ny * hw),
            (x1 + nx * hw, y1 + ny * hw)]


def convex_polygon_spans(points, clip: Rect):
    """Yield (y, x0, x1) integer spans for a convex polygon, clip-aware."""
    if len(points) < 3:
        return
    ys = [p[1] for p in points]
    y_lo = max(clip.y0, math.floor(min(ys)))
    y_hi = min(clip.y1, math.ceil(max(ys)))
    n = len(points)
    for y in range(y_lo, y_hi):
        yc = y + 0.5
        xs: list[float] = []
        for i in range(n):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n]
            if (ay <= yc < by) or (by <= yc < ay):
                t = (yc - ay) / (by - ay)
                xs.append(ax + t * (bx - ax))
        if len(xs) < 2:
            continue
        x0 = max(clip.x0, math.floor(min(xs)))
        x1 = min(clip.x1, math.ceil(max(xs)))
        if x1 > x0:
            yield y, x0, x1


def ellipse_spans(x0, y0, x1, y1, clip: Rect):
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    rx = max(1e-6, (x1 - x0) / 2)
    ry = max(1e-6, (y1 - y0) / 2)
    y_lo = max(clip.y0, math.floor(y0))
    y_hi = min(clip.y1, math.ceil(y1))
    for y in range(y_lo, y_hi):
        dy = (y + 0.5 - cy) / ry
        if abs(dy) > 1:
            continue
        half = rx * math.sqrt(1 - dy * dy)
        xa = max(clip.x0, math.floor(cx - half))
        xb = min(clip.x1, math.ceil(cx + half))
        if xb > xa:
            yield y, xa, xb


def _rounded_span(x0, y0, x1, y1, rad, yc):
    """Outer x span of a rounded rect at scan-line centre yc."""
    if yc < y0 or yc >= y1:
        return None, None
    inset = 0.0
    if rad > 0:
        if yc < y0 + rad:
            dy = yc - (y0 + rad)
            inset = rad - math.sqrt(max(0.0, rad * rad - dy * dy))
        elif yc >= y1 - rad:
            dy = yc - (y1 - rad)
            inset = rad - math.sqrt(max(0.0, rad * rad - dy * dy))
    return math.ceil(x0 + inset), math.floor(x1 - inset)

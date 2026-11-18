"""Retained rendering kernel.

Responsibilities:
  * keep per-object rendered layers in a cache keyed by
    (id, version, view_scale) - object identity is separate from cache
    entries, so deleting an object simply drops its entry and its old
    pixels are erased via the invalid region;
  * decide *which* rectangles actually need repainting after every edit,
    including stroke width, shadow blur pads and text overflow;
  * replay the full display-list paint order inside each dirty clip so
    translucent overlaps stay identical to a full-frame render;
  * expose real work counters for comparison against full-frame redraw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Rect, Transform, merge_rects
from .raster import Surface
from .displaylist import build_display_list, DisplayEntry, execute_command
from . import font as bmfont


@dataclass
class CacheEntry:
    key: tuple
    bounds: Rect
    layer: Surface


class RenderKernel:
    def __init__(self, scene, width: int, height: int,
                 bg=(245, 246, 250, 255)):
        self.scene = scene
        self.w = width
        self.h = height
        self.bg = bg
        self.transform = Transform(1.0, 24.0, 24.0)
        self.surface = Surface(width, height, bg)
        self.cache: dict[tuple[str, int], CacheEntry] = {}
        self.dirty: list[Rect] = [Rect(0, 0, width, height)]
        self.last_repainted: list[Rect] = []
        self.last_scroll_exposed: Rect | None = None
        self.last_display_list: list[DisplayEntry] = []

    # ------------------------------------------------------------ view ops
    def set_scale(self, scale: float, anchor=(None, None)) -> None:
        """Zoom invalidates the whole frame and every cached layer."""
        ax, ay = anchor
        if ax is None:
            ax, ay = self.w / 2, self.h / 2
        lx = self.transform.to_logical_x(ax)
        ly = self.transform.to_logical_y(ay)
        self.transform = Transform(
            scale, ax - lx * scale, ay - ly * scale)
        self.cache.clear()
        self.dirty = [Rect(0, 0, self.w, self.h)]

    def pan(self, dx_device: int, dy_device: int) -> None:
        """Pan keeps object layers cached; pixels are scrolled and only the
        exposed strip plus objects crossing it are repainted."""
        self.transform = Transform(self.transform.scale,
                                   self.transform.ox + dx_device,
                                   self.transform.oy + dy_device)
        exposed = self.surface.scroll(dx_device, dy_device, self.bg)
        self.last_scroll_exposed = exposed
        # The scroll copies pixels in whole device columns, but glyphs and
        # blurred shadow edges are rasterised against the new transform; at
        # integer boundaries a handful of edge columns can land one pixel
        # differently.  To stay byte-identical to a full-frame redraw, every
        # visible object's new bounds must be repainted too (background-only
        # regions are left as the scrolled pixels, so the repaint stays well
        # under a full frame).
        rects = list(self.dirty) + [exposed]
        for entry in build_display_list(self.scene, self.transform):
            rects.append(entry.bounds)
        self.dirty = merge_rects(rects, Rect(0, 0, self.w, self.h))

    def pan_to(self, ox: float, oy: float) -> None:
        dx, dy = round(ox - self.transform.ox), round(oy - self.transform.oy)
        if dx or dy:
            self.pan(dx, dy)

    # ------------------------------------------------------- invalidation
    def invalidate_objects(self, ids: list[tuple[str, int]],
                           old_bounds: list[Rect] | None = None) -> None:
        """Mark old and new bounds of edited objects dirty.

        Old bounds are essential: without them pixels left behind after a
        move/delete survive because the new geometry never covers them.
        """
        rects = list(self.dirty)
        if old_bounds:
            rects.extend(old_bounds)
        entries = self._current_entries()
        by_id = {(e.kind, e.obj_id): e for e in entries}
        for key in ids:
            entry = by_id.get(key)
            if entry is not None:
                rects.append(entry.bounds)
        self.dirty = merge_rects(rects, Rect(0, 0, self.w, self.h))

    def invalidate_all(self) -> None:
        self.dirty = [Rect(0, 0, self.w, self.h)]

    # ------------------------------------------------------------ culling
    def _current_entries(self) -> list[DisplayEntry]:
        return build_display_list(self.scene, self.transform)

    def _viewport(self) -> Rect:
        return Rect(0, 0, self.w, self.h)

    def _get_layer(self, entry: DisplayEntry) -> CacheEntry:
        key = (entry.kind, entry.obj_id)
        cache_key = (entry.obj_id, entry.version, round(self.transform.scale, 6))
        cached = self.cache.get(key)
        if cached is not None and cached.key == cache_key:
            return cached
        b = entry.bounds
        layer = Surface(max(1, b.width), max(1, b.height), bg=(0, 0, 0, 0))
        for cmd in entry.commands:
            p = self._shift_params(cmd, -b.x0, -b.y0)
            execute_command(layer, PaintCommandShim(cmd, p))
        ce = CacheEntry(cache_key, b, layer)
        self.cache[key] = ce
        return ce

    @staticmethod
    def _shift_params(cmd: "DisplayEntry", dx: int, dy: int):
        p = dict(cmd.params)
        if cmd.kind == "shadow":
            # Keep the same floor() the frame path uses; translate first so
            # the two paths round the identical real-valued coordinate.
            p["_origin"] = (math.floor(p["x0"] + dx) - p["blur"],
                            math.floor(p["y0"] + dy) - p["blur"])
            return p
        for name in ("x0", "y0", "x1", "y1", "x", "y"):
            if name in p:
                p[name] = p[name] + dx
        if "rect" in p:
            p["rect"] = p["rect"].shifted(dx, dy)
        if "polygon" in p:
            p["polygon"] = [(x + dx, y + dy) for x, y in p["polygon"]]
        return p

    # ------------------------------------------------------------- render
    def render_full(self) -> list[Rect]:
        """Reference path: rebuild everything, replay whole display list."""
        self.cache.clear()
        self.surface.clear(self.bg)
        entries = self._current_entries()
        self.last_display_list = entries
        for entry in entries:
            self._paint_entry_direct(entry, self._viewport())
        self.dirty = []
        self.last_repainted = [self._viewport()]
        return self.last_repainted

    def render_incremental(self) -> list[Rect]:
        """Partial path: erase dirty rects to background, then replay every
        paint command whose bounds intersect, in original paint order."""
        if not self.dirty:
            self.last_repainted = []
            return []
        entries = self._current_entries()
        self.last_display_list = entries
        living_keys = {(e.kind, e.obj_id) for e in entries}
        for key in list(self.cache):
            if key not in living_keys:
                del self.cache[key]

        dirty = merge_rects(self.dirty, self._viewport())
        # Recompose each dirty rect on an isolated surface initialised to the
        # background, replaying the full paint order clipped to that rect.
        # Commands are replayed directly (not merged via object layers):
        # source-over alpha is not associative, so blending a shadow and a
        # translucent fill on a transparent layer then blitting would differ
        # by a colour step from the full-frame direct path.  Per-object
        # cached layers remain available for pan/scroll reuse.
        dirty = merge_rects(self.dirty, self._viewport())
        # Warm the (id, version, scale) layer cache for pan/scroll and
        # identity checks; the actual repaint below replays commands
        # directly on the main surface inside a clip, which keeps source-over
        # alpha identical to the full-frame path (no intermediate surface).
        for entry in entries:
            self._get_layer(entry)
        for dr in dirty:
            # Erase the vacated pixels first so moved/deleted objects leave
            # no residue, then replay every intersecting entry in the global
            # paint order under a clip equal to the dirty rect.
            self.surface.fill_bg_rect(dr)
            self.surface.push_clip(dr)
            for entry in entries:
                if not entry.bounds.intersects(dr):
                    continue
                for cmd in entry.commands:
                    execute_command(self.surface, cmd)
            self.surface.pop_clip()
        self.dirty = []
        self.last_repainted = dirty
        return dirty

    def _paint_entry_direct(self, entry: DisplayEntry, clip: Rect) -> None:
        self.surface.push_clip(clip)
        for cmd in entry.commands:
            execute_command(self.surface, cmd)
        self.surface.pop_clip()

    # ------------------------------------------------------- overlays/info
    def draw_dirty_overlay(self, rects: list[Rect],
                           color=(255, 40, 40, 110),
                           outline=(200, 0, 0, 220)) -> None:
        for r in rects:
            self.surface.fill_rect(r.x0, r.y0, r.x1, r.y1, color)
            self.surface.fill_rect(r.x0, r.y0, r.x1, r.y0 + 2, outline)
            self.surface.fill_rect(r.x0, r.y1 - 2, r.x1, r.y1, outline)
            self.surface.fill_rect(r.x0, r.y0, r.x0 + 2, r.y1, outline)
            self.surface.fill_rect(r.x1 - 2, r.y0, r.x1, r.y1, outline)

    def display_list_dump(self) -> str:
        lines = []
        for i, e in enumerate(self.last_display_list):
            lines.append(
                f"[{i:02d}] {e.kind:4s} id={e.obj_id:<4d} v{e.version} "
                f"bounds=({e.bounds.x0},{e.bounds.y0},"
                f"{e.bounds.x1},{e.bounds.y1}) {e.label}")
            for c in e.commands:
                lines.append(f"       - {c.kind} color={c.color}")
        return "\n".join(lines)

    def stats(self) -> dict:
        d = self.surface.stats.as_dict()
        d["cached_layers"] = len(self.cache)
        d["dirty_rects"] = len(self.last_repainted)
        d["dirty_area_px"] = sum(r.area for r in self.last_repainted)
        d["frame_area_px"] = self.w * self.h
        return d

    def reset_stats(self) -> None:
        self.surface.reset_stats()

    def save(self, path: str) -> None:
        self.surface.write_ppm(path)


class PaintCommandShim:
    """Small adapter so cached layers reuse execute_command with shifted
    coordinates without mutating the original display-list command."""

    __slots__ = ("kind", "params", "color")

    def __init__(self, cmd, params):
        self.kind = cmd.kind
        self.params = params
        self.color = cmd.color

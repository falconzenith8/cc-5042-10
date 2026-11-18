"""Retained-mode editor controller.

The controller owns no widget code: it turns user intents into scene
mutations and precise kernel invalidations, and returns structured
results/errors so any interface (Tk view, headless REPL, scripts) can
present them.
"""
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect
from .renderer import RenderKernel


@dataclass
class HitResult:
    kind: str
    obj_id: int


class EditorError(Exception):
    """Surfaced to the retained drawing UI instead of crashing a session."""


class Editor:
    def __init__(self, scene, width: int, height: int, **kernel_kw):
        self.scene = scene
        self.kernel = RenderKernel(scene, width, height, **kernel_kw)
        self.message = "ready"
        self._drag_id: int | None = None
        self._grab_dx = 0.0
        self._grab_dy = 0.0
        self.kernel.render_incremental()

    # ------------------------------------------------------------- helpers
    def _device_to_logical(self, x: int, y: int):
        tf = self.kernel.transform
        return tf.to_logical_x(x), tf.to_logical_y(y)

    def _bounds_before(self, ids):
        """Device bounds of entries matching ids, using the current list."""
        out = []
        for e in self.kernel.last_display_list:
            if (e.kind, e.obj_id) in ids:
                out.append(e.bounds)
        return out

    # ----------------------------------------------------------- hit test
    def hit_test(self, x: int, y: int) -> HitResult | None:
        lx, ly = self._device_to_logical(x, y)
        top = None
        for node in self.scene.nodes.values():
            if node.contains(lx, ly):
                if top is None or (node.z, node.id) > (top.z, top.id):
                    top = node
        if top is not None:
            return HitResult("node", top.id)
        for edge in self.scene.edges.values():
            if self._edge_hit(edge, lx, ly):
                return HitResult("edge", edge.id)
        return None

    def _edge_hit(self, edge, lx, ly) -> bool:
        a = self.scene.nodes[edge.src]
        b = self.scene.nodes[edge.dst]
        ax, ay = a.cx, a.cy
        bx, by = b.cx, b.cy
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-9:
            return False
        t = max(0.0, min(1.0, ((lx - ax) * dx + (ly - ay) * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        tol = max(4.0 / self.kernel.transform.scale, edge.width + 3.0)
        return (px - lx) ** 2 + (py - ly) ** 2 <= tol * tol

    # ------------------------------------------------------------- editing
    def add_node(self, x: int, y: int, w: float, h: float, text: str = "",
                 shape: str = "round", **kw) -> int:
        try:
            lx, ly = self._device_to_logical(x, y)
            nid = self.scene.add_node(lx - w / 2, ly - h / 2, w, h, text,
                                      shape, **kw)
            self.kernel.invalidate_objects([("node", nid)])
            self.kernel.render_incremental()
            self.message = f"added node {nid}"
            return nid
        except Exception as exc:  # surfaced to the UI
            self.message = f"error: {exc}"
            raise EditorError(str(exc)) from exc

    def add_edge(self, src: int, dst: int, text: str = "", **kw) -> int:
        try:
            eid = self.scene.add_edge(src, dst, text, **kw)
            self.kernel.invalidate_objects([("edge", eid)])
            self.kernel.render_incremental()
            self.message = f"added edge {eid}"
            return eid
        except Exception as exc:
            self.message = f"error: {exc}"
            raise EditorError(str(exc)) from exc

    def delete(self, kind: str, oid: int) -> None:
        try:
            ids = {(kind, oid)}
            if kind == "node":
                for eid in self.scene.connected_edges(oid):
                    ids.add(("edge", eid))
            old = self._bounds_before(ids)
            if kind == "node":
                self.scene.remove_node(oid)
            else:
                self.scene.remove_edge(oid)
            # Old bounds must be erased even though the object is gone.
            self.kernel.invalidate_objects([], old_bounds=old)
            self.kernel.render_incremental()
            self.message = f"deleted {kind} {oid}"
        except Exception as exc:
            self.message = f"error: {exc}"
            raise EditorError(str(exc)) from exc

    def set_text(self, kind: str, oid: int, text: str) -> None:
        try:
            ids = {(kind, oid)}
            old = self._bounds_before(ids)
            if kind == "node":
                self.scene.set_node_text(oid, text)
            else:
                self.scene.edges[oid].text = text
                self.scene.edges[oid].bump()
            self.kernel.invalidate_objects(list(ids), old_bounds=old)
            self.kernel.render_incremental()
            self.message = f"text updated on {kind} {oid}"
        except Exception as exc:
            self.message = f"error: {exc}"
            raise EditorError(str(exc)) from exc

    # --------------------------------------------------------- drag & drop
    def begin_drag(self, x: int, y: int) -> HitResult | None:
        hit = self.hit_test(x, y)
        if hit is None or hit.kind != "node":
            self.message = "nothing draggable here"
            return None
        node = self.scene.nodes[hit.obj_id]
        lx, ly = self._device_to_logical(x, y)
        self._drag_id = hit.obj_id
        self._grab_dx = lx - node.x
        self._grab_dy = ly - node.y
        self.message = f"dragging node {hit.obj_id}"
        return hit

    def drag_to(self, x: int, y: int) -> None:
        if self._drag_id is None:
            return
        try:
            nid = self._drag_id
            ids = {("node", nid)}
            for eid in self.scene.connected_edges(nid):
                ids.add(("edge", eid))
            old = self._bounds_before(ids)
            lx, ly = self._device_to_logical(x, y)
            self.scene.move_node(nid, lx - self._grab_dx,
                                 ly - self._grab_dy)
            self.kernel.invalidate_objects(list(ids), old_bounds=old)
            self.kernel.render_incremental()
        except Exception as exc:
            self.message = f"error: {exc}"
            raise EditorError(str(exc)) from exc

    def end_drag(self) -> None:
        if self._drag_id is not None:
            self.message = f"dropped node {self._drag_id}"
        self._drag_id = None

    # --------------------------------------------------------- navigation
    def pan(self, dx: int, dy: int) -> None:
        self.kernel.pan(dx, dy)
        self.kernel.render_incremental()
        self.message = f"panned ({dx},{dy})"

    def zoom(self, factor: float, anchor=(None, None)) -> None:
        new_scale = min(8.0, max(0.15,
                         self.kernel.transform.scale * factor))
        self.kernel.set_scale(new_scale, anchor)
        self.kernel.render_incremental()
        self.message = f"zoom {new_scale:.2f}x"

    def full_redraw_reference(self) -> None:
        """Force the full-frame reference path (for verification)."""
        self.kernel.render_full()
        self.message = "full frame reference redraw"

    def verify_against_full_frame(self) -> dict:
        """Recompute the current scene on an independent surface and compare
        every RGBA byte with the retained kernel's surface.

        Returns measured evidence (mismatch count, dirty area, work stats)
        instead of a boolean guess, so the editing window can show the user
        exactly what the retained repaint produced.
        """
        from .raster import Surface
        from .displaylist import build_display_list, execute_command
        ref = Surface(self.kernel.w, self.kernel.h, self.kernel.bg)
        for entry in build_display_list(self.scene, self.kernel.transform):
            for cmd in entry.commands:
                execute_command(ref, cmd)
        a, b = self.kernel.surface.buf, ref.buf
        mismatch = sum(1 for i in range(0, len(a), 4)
                       if a[i:i + 4] != b[i:i + 4])
        dirty = self.kernel.last_repainted
        evidence = {
            "pixel_mismatch": mismatch,
            "pixel_match": mismatch == 0,
            "dirty_rects": [(r.x0, r.y0, r.x1, r.y1) for r in dirty],
            "dirty_area_px": sum(r.area for r in dirty),
            "frame_area_px": self.kernel.w * self.kernel.h,
            "work": self.kernel.stats(),
        }
        self.message = ("pixel-identical to full frame"
                        if mismatch == 0
                        else f"MISMATCH {mismatch} px vs full frame")
        return evidence

    def compare_update_strategies(self, edit=None) -> dict:
        """Replay one edit through both granularity prototypes on a scene
        clone and return measured repaint area + pixel evidence.

        ``edit`` is ``("move", nid, dx, dy)`` or ``("set_text", nid, text)``;
        when omitted the current scene state alone is compared.  The
        production kernel's surface is never touched.
        """
        from .updaters import (NodeDirtyUpdater, RegionPropagationUpdater,
                               NaiveNodeDirtyUpdater)
        from .cli import _rebuild_like
        clone = _rebuild_like(self.scene)
        common = dict(width=self.kernel.w, height=self.kernel.h,
                      transform=self.kernel.transform)
        nd = NodeDirtyUpdater(clone, **common)
        nv = NaiveNodeDirtyUpdater(clone, **common)
        rp = RegionPropagationUpdater(clone, **common)

        keys = set()
        old_nd = old_nv = old_rp = []
        if edit is not None:
            op = edit[0]
            if op == "move":
                _, nid, dx, dy = edit
                keys = {("node", nid)}
                for eid in clone.connected_edges(nid):
                    keys.add(("edge", eid))
                old_nd, old_nv, old_rp = (nd.bounds_of(keys),
                                          nv.bounds_of(keys),
                                          rp.bounds_of(keys))
                node = clone.nodes[nid]
                clone.move_node(nid, node.x + dx, node.y + dy)
            elif op == "set_text":
                _, nid, text = edit
                keys = {("node", nid)}
                old_nd, old_nv, old_rp = (nd.bounds_of(keys),
                                          nv.bounds_of(keys),
                                          rp.bounds_of(keys))
                clone.set_node_text(nid, text)
            else:
                raise EditorError(f"unknown compare edit {op}")
            nd.flag(keys, old_nd)
            nv.flag(keys, old_nv)
            rp.invalidate(keys, old_rp)

        results = {"node_dirty": nd.update(),
                   "naive_node_dirty": nv.update(),
                   "region_propagation": rp.update()}
        ref = rp.render_full_reference()
        out = {}
        for name, res in results.items():
            up = {"node_dirty": nd, "naive_node_dirty": nv,
                  "region_propagation": rp}[name]
            mm = sum(1 for i in range(0, len(up.surface.buf), 4)
                     if up.surface.buf[i:i + 4] != ref.buf[i:i + 4])
            out[name] = {
                "pixel_mismatch": mm,
                "pixel_match": mm == 0,
                "dirty_rects": len(res.rects),
                "dirty_area_px": res.dirty_area_px,
                "painted_px": res.painted_px,
                "fell_back": res.fell_back,
                "fallback_reason": res.fallback_reason,
            }
        return out

    # --------------------------------------------------------------- info
    def dirty_rects(self) -> list[Rect]:
        return list(self.kernel.last_repainted)

    def display_list_text(self) -> str:
        return self.kernel.display_list_dump()

    def stats(self) -> dict:
        return self.kernel.stats()

    def save(self, path: str) -> None:
        self.kernel.save(path)

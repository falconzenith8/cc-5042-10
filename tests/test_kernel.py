"""Correctness tests for the retained rendering kernel.

Run with: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowkern import (Scene, Editor, RenderKernel, Rect, build_demo_scene,
                      build_dense_scene)
from flowkern.raster import Surface, line_quad, convex_polygon_spans
from flowkern.geometry import merge_rects, Transform
from flowkern import font as bmfont


def buf(kernel):
    return bytes(kernel.surface.buf)


class GeometryTests(unittest.TestCase):
    def test_outward_rounding_never_shrinks(self):
        r = Rect.from_ltrb(1.2, 1.8, 4.7, 6.2)
        self.assertEqual((r.x0, r.y0, r.x1, r.y1), (1, 1, 5, 7))

    def test_merge_keeps_disjoint(self):
        vp = Rect(0, 0, 100, 100)
        rects = merge_rects([Rect(0, 0, 10, 10), Rect(80, 80, 90, 90)], vp)
        self.assertEqual(len(rects), 2)

    def test_merge_overlapping_unions(self):
        vp = Rect(0, 0, 100, 100)
        rects = merge_rects([Rect(0, 0, 20, 20), Rect(10, 10, 30, 30)], vp)
        self.assertEqual(rects, [Rect(0, 0, 30, 30)])


class RasterTests(unittest.TestCase):
    def test_clip_stack(self):
        s = Surface(50, 50)
        s.push_clip(Rect(10, 10, 20, 20))
        s.fill_rect(0, 0, 50, 50, (255, 0, 0, 255))
        s.pop_clip()
        self.assertEqual(s.pixel(5, 5), s.bg)
        self.assertEqual(s.pixel(15, 15), (255, 0, 0, 255))

    def test_alpha_blend_against_reference(self):
        s = Surface(4, 1)
        s.fill_rect(0, 0, 4, 1, (0, 0, 0, 255))
        s.fill_rect(1, 0, 3, 1, (255, 255, 255, 128))
        # 50% white over black -> ~127
        self.assertAlmostEqual(s.pixel(2, 0)[0], 127, delta=1)
        self.assertEqual(s.pixel(0, 0), (0, 0, 0, 255))

    def test_line_quad_half_width(self):
        q = line_quad(0, 0, 100, 0, 10)
        xs = [p[0] for p in q]
        ys = [p[1] for p in q]
        self.assertEqual(min(ys), -5)
        self.assertEqual(max(ys), 5)
        self.assertEqual(min(xs), 0)
        self.assertEqual(max(xs), 100)

    def test_polygon_spans_clipped(self):
        pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        spans = list(convex_polygon_spans(pts, Rect(2, 2, 6, 6)))
        self.assertTrue(spans)
        for y, x0, x1 in spans:
            self.assertGreaterEqual(x0, 2)
            self.assertLessEqual(x1, 6)

    def test_font_unknown_glyph_tofu_measurable(self):
        w_known, _ = bmfont.text_size("A", 1.0)
        w_unknown, _ = bmfont.text_size("中", 1.0)
        self.assertGreater(w_unknown, 0)
        # One glyph: its 5 columns plus the final one-column overflow pad.
        self.assertEqual(w_known, bmfont.GLYPH_W + 1)


class BoundsTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()

    def test_bounds_include_stroke_shadow_and_text_overflow(self):
        nid = self.scene.add_node(100, 100, 80, 40, "VERY LONG LABEL XYZ")
        e = Editor(self.scene, 600, 400)
        entry = next(x for x in e.kernel.last_display_list
                     if x.kind == "node" and x.obj_id == nid)
        node = self.scene.nodes[nid]
        tf = e.kernel.transform
        body = tf.logical_rect_to_device(node.x, node.y, node.x + node.w,
                                         node.y + node.h)
        # bounds must extend beyond the body on every side (shadow pad/stroke)
        self.assertLess(entry.bounds.x0, body.x0)
        self.assertLess(entry.bounds.y0, body.y0)
        self.assertGreater(entry.bounds.x1, body.x1)
        self.assertGreater(entry.bounds.y1, body.y1)

    def test_edge_bounds_cover_arrow_and_label(self):
        a = self.scene.add_node(50, 50, 60, 40, "A")
        b = self.scene.add_node(400, 300, 60, 40, "B")
        self.scene.add_edge(a, b, text="LABEL")
        e = Editor(self.scene, 700, 500)
        edge_entry = next(x for x in e.kernel.last_display_list
                          if x.kind == "edge")
        kinds = [c.kind for c in edge_entry.commands]
        self.assertIn("marker", kinds)
        self.assertIn("text", kinds)
        self.assertGreater(edge_entry.bounds.area, 0)


class InvalidationTests(unittest.TestCase):
    def test_move_clears_old_pixels(self):
        scene = Scene()
        nid = scene.add_node(100, 100, 80, 50, "MOVE ME",
                             fill=(10, 20, 30, 255))
        e = Editor(scene, 500, 400)
        old_entry = next(x for x in e.kernel.last_display_list
                         if x.obj_id == nid)
        old_spot = (old_entry.bounds.x0 + 2, old_entry.bounds.y0 + 2)
        self.assertNotEqual(e.kernel.surface.pixel(*old_spot),
                            e.kernel.surface.bg)
        node = scene.nodes[nid]
        ids = {("node", nid)}
        old_bounds = [old_entry.bounds]
        scene.move_node(nid, node.x + 220, node.y + 140)
        e.kernel.invalidate_objects(list(ids), old_bounds=old_bounds)
        e.kernel.render_incremental()
        # every pixel of the old bounds must be background again
        r = old_entry.bounds
        for y in range(r.y0, r.y1, 3):
            for x in range(r.x0, r.x1, 3):
                self.assertEqual(e.kernel.surface.pixel(x, y),
                                 e.kernel.surface.bg,
                                 f"stale pixel at {x},{y}")

    def test_delete_erases_old_position(self):
        scene = Scene()
        nid = scene.add_node(120, 120, 90, 60, "DELETE",
                             fill=(200, 30, 30, 255))
        e = Editor(scene, 500, 400)
        r = next(x for x in e.kernel.last_display_list
                 if x.obj_id == nid).bounds
        e.delete("node", nid)
        for y in range(r.y0, r.y1, 2):
            for x in range(r.x0, r.x1, 2):
                self.assertEqual(e.kernel.surface.pixel(x, y),
                                 e.kernel.surface.bg)
        self.assertNotIn(("node", nid), e.kernel.cache)

    def test_dirty_region_is_local_not_centre_rect(self):
        scene = build_demo_scene()
        e = Editor(scene, 1120, 460)
        target = next(iter(scene.nodes))
        node = scene.nodes[target]
        before = list(e.kernel.last_repainted)
        e.kernel.reset_stats()
        ids = {("node", target)}
        for eid in scene.connected_edges(target):
            ids.add(("edge", eid))
        old = [x.bounds for x in e.kernel.last_display_list
               if (x.kind, x.obj_id) in ids]
        scene.move_node(target, node.x + 30, node.y + 20)
        e.kernel.invalidate_objects(list(ids), old_bounds=old)
        rects = e.kernel.render_incremental()
        total = sum(r.area for r in rects)
        self.assertLess(total, 0.25 * 1120 * 460)


class PaintOrderTests(unittest.TestCase):
    def test_order_by_z_and_id_never_colour(self):
        scene = Scene()
        a = scene.add_node(20, 20, 100, 60, "A", fill=(255, 0, 0, 255))
        b = scene.add_node(30, 30, 100, 60, "B", fill=(0, 0, 255, 255))
        e = Editor(scene, 400, 300)
        order = [(x.kind, x.obj_id) for x in e.kernel.last_display_list]
        self.assertEqual(order, scene.display_order())
        scene.set_z(a, 5)
        e.kernel.invalidate_all()
        e.kernel.render_incremental()
        order2 = [x.obj_id for x in e.kernel.last_display_list
                  if x.kind == "node"]
        self.assertEqual(order2[-1], a)


class TransparencyTests(unittest.TestCase):
    @staticmethod
    def _full(scene, w=500, h=400):
        r = RenderKernel(scene, w, h)
        r.render_full()
        return buf(r)

    def test_partial_matches_full_with_translucent_overlap(self):
        scene = Scene()
        a = scene.add_node(60, 60, 160, 90, "ALPHA 1",
                           fill=(80, 160, 255, 110),
                           stroke=(30, 90, 200, 180))
        b = scene.add_node(120, 100, 160, 90, "ALPHA 2",
                           fill=(255, 120, 200, 110),
                           stroke=(200, 40, 130, 180))
        e = Editor(scene, 500, 400)
        self.assertEqual(buf(e.kernel), self._full(scene))
        e.set_text("node", b, "CHANGED LABEL")
        self.assertEqual(buf(e.kernel), self._full(scene))

    def test_incremental_pixel_identical_to_full_frame(self):
        ref = RenderKernel(build_demo_scene(), 1120, 460)
        ref.render_full()
        inc = RenderKernel(build_demo_scene(), 1120, 460)
        inc.render_incremental()
        self.assertEqual(buf(ref), buf(inc))


class NavigationTests(unittest.TestCase):
    def test_pan_scrolls_and_repaints_only_exposed(self):
        scene = build_demo_scene()
        e = Editor(scene, 800, 400)
        e.kernel.reset_stats()
        e.pan(60, 0)
        st = e.kernel.stats()
        self.assertGreater(st["scroll_px"], 0)
        self.assertLess(st["dirty_area_px"], 800 * 400)

    def test_zoom_invalidates_all_and_cache(self):
        scene = build_demo_scene()
        e = Editor(scene, 800, 400)
        self.assertGreater(len(e.kernel.cache), 0)
        e.zoom(1.5)
        self.assertEqual(e.kernel.dirty, [])
        self.assertEqual(e.kernel.last_repainted,
                         [Rect(0, 0, 800, 400)])
        # cache was rebuilt at the new scale
        self.assertGreater(len(e.kernel.cache), 0)

    def test_pan_is_pixel_identical_to_full_redraw(self):
        from flowkern import Transform
        e = Editor(build_demo_scene(), 900, 420)
        e.pan(70, 0)
        e.pan(0, 45)
        tf = e.kernel.transform
        ref = RenderKernel(build_demo_scene(), 900, 420)
        ref.transform = Transform(tf.scale, tf.ox, tf.oy)
        ref.render_full()
        self.assertEqual(buf(e.kernel), buf(ref))
        # object-area repaint still leaves real scroll work on record
        self.assertGreater(e.kernel.stats()["scroll_px"], 0)


class CacheIdentityTests(unittest.TestCase):
    def test_cache_key_uses_version_unmoved_layer_reused(self):
        scene = build_demo_scene()
        e = Editor(scene, 800, 400)
        target = next(iter(scene.nodes))
        untouched = [n for n in scene.nodes if n != target][0]
        key = ("node", untouched)
        layer_before = e.kernel.cache[key].layer
        node = scene.nodes[target]
        ids = {("node", target)}
        for eid in scene.connected_edges(target):
            ids.add(("edge", eid))
        old = [x.bounds for x in e.kernel.last_display_list
               if (x.kind, x.obj_id) in ids]
        scene.move_node(target, node.x + 25, node.y + 15)
        e.kernel.invalidate_objects(list(ids), old_bounds=old)
        e.kernel.render_incremental()
        self.assertIs(e.kernel.cache[key].layer, layer_before)


class StressComparisonTests(unittest.TestCase):
    def test_dense_edits_stay_correct_and_cheaper(self):
        scene = build_dense_scene(6, 6, spacing_x=140, spacing_y=90)
        viewport = 700 * 420
        ref = RenderKernel(build_dense_scene(6, 6, spacing_x=140,
                                             spacing_y=90), 700, 420)
        ref.render_full()
        full_px = ref.stats()["painted_px"]

        inc = Editor(scene, 700, 420)
        self.assertEqual(buf(inc.kernel), buf(ref))
        ids_all = list(scene.nodes)[:12]
        partial_total = 0
        for i, nid in enumerate(ids_all):
            node = scene.nodes[nid]
            ids = {("node", nid)}
            for eid in scene.connected_edges(nid):
                ids.add(("edge", eid))
            old = [x.bounds for x in inc.kernel.last_display_list
                   if (x.kind, x.obj_id) in ids]
            scene.move_node(nid, node.x + 40, node.y - 30)
            inc.kernel.invalidate_objects(list(ids), old_bounds=old)
            inc.kernel.reset_stats()
            inc.kernel.render_incremental()
            partial_total += inc.kernel.stats()["painted_px"]
        self.assertLess(partial_total, full_px * len(ids_all))
        # final state pixel-identical to independent full render
        from flowkern.cli import _rebuild_like
        verify = RenderKernel(_rebuild_like(scene), 700, 420)
        verify.render_full()
        self.assertEqual(buf(verify), buf(inc.kernel))


if __name__ == "__main__":
    unittest.main(verbosity=2)

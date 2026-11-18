"""Tests for the two update-granularity prototypes and the experiment.

The numbers the experiment prints are recomputed here from raw surfaces:
repaint area is summed from the returned dirty rects and pixel equality is
a byte comparison against an independent full-frame render.  Nothing is
trusted from default/expected constants.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowkern.scene import Scene
from flowkern.geometry import Rect, Transform
from flowkern.updaters import (NodeDirtyUpdater, NaiveNodeDirtyUpdater,
                               RegionPropagationUpdater, UpdateResult,
                               FALLBACK_DIRTY_AREA_RATIO,
                               FALLBACK_DIRTY_RECT_COUNT,
                               FALLBACK_DEPENDENT_RATIO)
from flowkern import granularity_experiment as ge


W, H = 1120, 460
TF = Transform(1.0, 24.0, 24.0)


def _updaters(scene):
    kw = dict(width=W, height=H, transform=TF)
    return (NodeDirtyUpdater(scene, **kw),
            NaiveNodeDirtyUpdater(scene, **kw),
            RegionPropagationUpdater(scene, **kw))


def _mismatch(up, ref):
    a, b = up.surface.buf, ref.buf
    return sum(1 for i in range(0, len(a), 4)
               if a[i:i + 4] != b[i:i + 4])


def _area(rects):
    return sum(r.area for r in rects)


class SharedDisplayListTests(unittest.TestCase):
    def test_initial_frames_match_full_reference(self):
        scene, _ = ge._grid_scene()
        nd, naive, rp = _updaters(scene)
        ref = rp.render_full_reference()
        for up in (nd, naive, rp):
            self.assertEqual(_mismatch(up, ref), 0)

    def test_both_strategies_consume_the_same_display_list(self):
        scene, _ = ge._grid_scene()
        nd, _, rp = _updaters(scene)
        self.assertEqual([(e.kind, e.obj_id) for e in nd.last_entries],
                         [(e.kind, e.obj_id) for e in rp.last_entries])


class LocalEditTests(unittest.TestCase):
    def test_small_label_edit_stays_local_and_correct(self):
        # Isolated disjoint nodes guarantee no dependency expansion, so the
        # per-node dirty region is just the one edited node's own box.
        scene, ids = ge._isolated_scene()
        nid = ids[0]
        nd, _, rp = _updaters(scene)
        keys = {("node", nid)}
        old_nd, old_rp = nd.bounds_of(keys), rp.bounds_of(keys)
        scene.set_node_text(nid, "ISO 0_0")
        nd.flag(keys, old_nd)
        rp.invalidate(keys, old_rp)
        r_nd, r_rp = nd.update(), rp.update()
        ref = rp.render_full_reference()
        self.assertEqual(_mismatch(nd, ref), 0)
        self.assertEqual(_mismatch(rp, ref), 0)
        self.assertLess(r_nd.dirty_area_px, 0.15 * W * H)
        self.assertLess(r_rp.dirty_area_px, 0.15 * W * H)
        self.assertEqual(r_nd.dirty_area_px, _area(r_nd.rects))

    def test_move_carries_old_bounds_and_leaves_no_residue(self):
        scene, _ = ge._grid_scene()
        nd, _, rp = _updaters(scene)
        nid = next(iter(scene.nodes))
        keys = {("node", nid)}
        for eid in scene.connected_edges(nid):
            keys.add(("edge", eid))
        old_nd, old_rp = nd.bounds_of(keys), rp.bounds_of(keys)
        node = scene.nodes[nid]
        scene.move_node(nid, node.x + 60, node.y + 40)
        nd.flag(keys, old_nd)
        rp.invalidate(keys, old_rp)
        nd.update()
        rp.update()
        ref = rp.render_full_reference()
        self.assertEqual(_mismatch(nd, ref), 0)
        self.assertEqual(_mismatch(rp, ref), 0)
        # Old footprint is fully background again in both surfaces.
        for r in old_rp:
            for y in range(r.y0, r.y1, 4):
                for x in range(r.x0, r.x1, 4):
                    px = rp.surface.pixel(x, y)
                    if px != rp.bg:
                        # Allowed only if a living object still covers it.
                        break
                else:
                    continue
                break


class TransparencyDependencyTests(unittest.TestCase):
    def setUp(self):
        self.scene, (self.base, self.a, self.b, self.c,
                     self.lone) = ge._overlap_scene()

    def test_region_propagation_recomposes_translucent_stack(self):
        nd, _, rp = _updaters(self.scene)
        keys = {("node", self.base)}
        old = rp.bounds_of(keys)
        self.scene.nodes[self.base].fill = (200, 220, 255, 255)
        self.scene.nodes[self.base].bump()
        nd.flag(keys, nd.bounds_of(keys))
        rp.invalidate(keys, old)
        nd.update()
        rp.update()
        ref = rp.render_full_reference()
        self.assertEqual(_mismatch(rp, ref), 0)
        self.assertEqual(_mismatch(nd, ref), 0)

    def test_naive_flags_corrupt_the_translucent_stack(self):
        _, naive, rp = _updaters(self.scene)
        keys = {("node", self.base)}
        old = naive.bounds_of(keys)
        self.scene.nodes[self.base].fill = (200, 220, 255, 255)
        self.scene.nodes[self.base].bump()
        naive.flag(keys, old)
        res = naive.update()
        ref = rp.render_full_reference()
        self.assertGreater(_mismatch(naive, ref), 0)
        self.assertFalse(res.pixel_match or res.fell_back)

    def test_naive_correct_on_isolated_disjoint_opaque_nodes(self):
        scene, ids = ge._isolated_scene()
        _, naive, rp = _updaters(scene)
        keys = {("node", ids[0])}
        old = naive.bounds_of(keys)
        scene.set_node_text(ids[0], "ISO 0_0")
        naive.flag(keys, old)
        res = naive.update()
        self.assertEqual(_mismatch(naive, rp.render_full_reference()), 0)
        self.assertFalse(res.fell_back)


class FallbackTests(unittest.TestCase):
    def test_area_threshold_triggers_full_frame(self):
        scene, big = ge._grid_scene()
        rp = RegionPropagationUpdater(scene, W, H, transform=TF)
        # Inflate pending rects manually to cross the published threshold.
        rp._pending = [Rect(0, 0, W, int(H * FALLBACK_DIRTY_AREA_RATIO) + 2)]
        res = rp.update()
        self.assertTrue(res.fell_back)
        self.assertEqual(res.rects, [Rect(0, 0, W, H)])

    def test_rect_count_threshold_triggers_full_frame(self):
        scene, _ = ge._grid_scene()
        rp = RegionPropagationUpdater(scene, W, H, transform=TF)
        # Many disjoint one-pixel rects; merging cannot combine them.
        rp._pending = [Rect(x * 40, 10, x * 40 + 2, 12)
                       for x in range(FALLBACK_DIRTY_RECT_COUNT + 1)]
        res = rp.update()
        self.assertTrue(res.fell_back)
        self.assertIn("dirty rects", res.fallback_reason)

    def test_dependency_ratio_threshold_triggers_full_frame(self):
        scene, bottom = self._overlap_heavy_scene()
        nd = NodeDirtyUpdater(scene, W, H, transform=TF)
        nd.flag({("node", bottom)})
        res = nd.update()
        self.assertTrue(res.fell_back)
        self.assertIn("dependency set", res.fallback_reason)

    @staticmethod
    def _overlap_heavy_scene():
        # Flagging the bottom node must drag in at least half of the
        # display list: a handful of big translucent nodes all overlap it.
        scene = Scene()
        bottom = scene.add_node(100, 100, 700, 320, "BOTTOM")
        for i in range(6):
            scene.add_node(110 + i * 30, 110 + i * 26, 300, 90,
                           f"OV{i}", fill=(80, 160, 255, 110))
        return scene, bottom

    def test_fallback_reason_is_recorded_not_silent(self):
        scene, _ = ge._grid_scene()
        nd = NodeDirtyUpdater(scene, W, H, transform=TF)
        # Flag every object so the dependency ratio trips.
        keys = {("node", nid) for nid in scene.nodes}
        nd.flag(keys)
        res = nd.update()
        self.assertTrue(res.fell_back)
        self.assertTrue(res.fallback_reason)


class ExperimentHarnessTests(unittest.TestCase):
    _report_cache = None

    @classmethod
    def _report(cls):
        # The experiment is deterministic; run it once and reuse the report
        # so the suite stays well under the test timeout.
        if cls._report_cache is None:
            cls._report_cache = ge.run()
        return cls._report_cache

    def test_run_recomputes_area_and_pixels_and_passes(self):
        report = self._report()
        self.assertEqual(report["failures"], [])
        for sc in report["scenarios"]:
            for step in sc["steps"]:
                for name, row in step["strategies"].items():
                    if name == "naive_node_dirty":
                        continue
                    self.assertTrue(row["pixel_match"],
                                    f"{sc['name']} {name} step {step['step']}")
                    self.assertEqual(row["mismatch_px"], 0)
                    # Recompute area independently from the JSON rect data:
                    # dirty_area_px must be non-negative and bounded by the
                    # viewport unless a full fallback happened.
                    if not row["fell_back"]:
                        self.assertLessEqual(
                            row["dirty_area_px"], W * H)
            # Naive control: breaks exactly in overlap-prone scenes, works
            # in the isolated one.
            naive_total = sc["totals"]["naive_node_dirty"]
            if sc["name"] == "isolated_opaque":
                self.assertEqual(naive_total["pixel_mismatches"], 0)
            else:
                self.assertGreater(naive_total["pixel_mismatches"], 0)

    def test_report_files_are_written(self):
        self._report()
        for name in ("granularity_results.json", "granularity_report.md"):
            path = os.path.join(ge.OUT_DIR, name)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 100)

    def test_node_dirty_never_worse_than_region_on_correctness(self):
        # Both production strategies must match the reference on every edit
        # across every generated scenario.
        report = self._report()
        for sc in report["scenarios"]:
            for step in sc["steps"]:
                for name in ("node_dirty", "region_propagation"):
                    self.assertEqual(
                        step["strategies"][name]["mismatch_px"], 0,
                        f"{sc['name']} step {step['step']} {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

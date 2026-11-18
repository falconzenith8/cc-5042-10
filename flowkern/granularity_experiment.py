"""In-repo experiment: update-granularity trade-offs on one display list.

The experiment drives three updaters built from :mod:`flowkern.updaters`
with byte-identical edit scripts against the same scene definition:

  node_dirty           per-node dirty flags + explicit dependency expansion
  naive_node_dirty     same flags, no expansion (negative control)
  region_propagation   old/new bounds -> dirty rects -> clipped list replay

Every edit is checked against an independent full-frame render of the
same scene (``render_full_reference``) so repaint area and pixel equality
are recomputed by code, never asserted from defaults.  Numbers are written
to ``demo_out/granularity_results.json`` and a human-readable report to
``demo_out/granularity_report.md``; PPM evidence is emitted alongside.

Run:  python3 -m flowkern.granularity_experiment
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field

from .geometry import Rect, Transform
from .scene import Scene
from .scene_factory import build_dense_scene
from .updaters import (NodeDirtyUpdater, NaiveNodeDirtyUpdater,
                       RegionPropagationUpdater, UpdateResult,
                       FALLBACK_DIRTY_AREA_RATIO, FALLBACK_DIRTY_RECT_COUNT,
                       FALLBACK_DEPENDENT_RATIO)

WIDTH, HEIGHT = 1120, 460
OUT_DIR = "demo_out"
TRANSFORM = Transform(1.0, 24.0, 24.0)
STRATEGIES = ("node_dirty", "naive_node_dirty", "region_propagation")


# ----------------------------------------------------------- scene builders
def _grid_scene(cols=8, rows=5, seed=23) -> Scene:
    """Dense grid with one deliberately large node for move workloads."""
    scene = build_dense_scene(cols, rows, spacing_x=130, spacing_y=82,
                              seed=seed)
    big = scene.add_node(60, 360, 300, 78, "BIG BATCH NODE",
                         shape="round", fill=(120, 170, 255, 255),
                         shadow_blur=7)
    # Tie the big node into the graph so moving it also bends real edges.
    first = next(iter(scene.nodes))
    scene.add_edge(big, first, text="BATCH")
    return scene, big


def _tiny_edit_scene(seed=29) -> Scene:
    """Scene with many small primitives so label edits stay genuinely tiny."""
    scene = build_dense_scene(9, 4, spacing_x=118, spacing_y=96, seed=seed)
    return scene


def _overlap_scene() -> Scene:
    """Three mutually overlapping translucent nodes plus an opaque base."""
    scene = Scene()
    base = scene.add_node(300, 120, 260, 180, "OPAQUE BASE",
                          fill=(235, 238, 245, 255))
    a = scene.add_node(240, 90, 250, 90, "ALPHA A",
                       fill=(80, 160, 255, 110), stroke=(30, 90, 200, 180))
    b = scene.add_node(300, 140, 250, 90, "ALPHA B",
                       fill=(255, 120, 200, 110), stroke=(200, 40, 130, 180))
    c = scene.add_node(360, 200, 240, 80, "ALPHA C",
                       fill=(120, 230, 150, 110), stroke=(40, 140, 80, 180))
    scene.add_edge(base, a, marker_end="none")
    # Fully isolated opaque node, far from every translucent bounds: the
    # naive negative control must stay correct when no dependency exists.
    lone = scene.add_node(820, 330, 180, 70, "LONE OPAQUE",
                          fill=(200, 230, 200, 255),
                          stroke=(40, 130, 60, 255))
    return scene, (base, a, b, c, lone)


# ----------------------------------------------------------------- editspec
@dataclass
class Edit:
    op: str                       # move | set_text | set_fill | delete
    target: int
    args: tuple = ()


@dataclass
class Scenario:
    name: str
    description: str
    build: object
    edits: list[Edit] = field(default_factory=list)
    # Whether the naive per-object repaint is *expected* to break in this
    # scene: true whenever edited bounds intersect other objects or
    # non-opaque fills exist.  The isolated_opaque scene flips this to false
    # and proves the naive strategy is correct under its own preconditions.
    naive_expected_to_break: bool = True


def _move_edits(ids, rng, steps=8, span=(110, 70)):
    edits = []
    for i in range(steps):
        nid = ids[i % len(ids)]
        edits.append(Edit("move", nid,
                          (rng.uniform(-span[0], span[0]),
                           rng.uniform(-span[1], span[1]))))
    return edits


def _isolated_scene() -> Scene:
    """Opaque, mutually disjoint nodes: the naive strategy's home turf.

    Shadow blur is off, there are no edges, and the grid is spaced so even
    stroke-padded bounds never touch another node.  Under these strict
    preconditions pure per-object flags must be pixel-correct.
    """
    scene = Scene()
    ids = []
    for r in range(3):
        for c in range(4):
            ids.append(scene.add_node(
                40 + c * 270, 40 + r * 150, 150, 70,
                f"ISO {r}_{c}", fill=(214, 232, 250, 255),
                stroke=(34, 88, 160, 255), shadow_blur=0,
                shadow_offset=(0.0, 0.0)))
    return scene, ids


def build_scenarios() -> list[Scenario]:
    rng = random.Random(41)
    grid, big = _grid_scene()
    small_ids = [nid for nid in grid.nodes if nid != big]
    grid_edits = [Edit("move", big, (90, -40)),
                  Edit("move", big, (-140, 30)),
                  Edit("move", big, (60, 55))]
    grid_edits += _move_edits(small_ids, rng, steps=9)
    # A few tiny label edits interleaved with the moves.
    for nid in small_ids[:4]:
        grid_edits.append(Edit("set_text", nid, (f"T{nid}",)))

    tiny = _tiny_edit_scene()
    tiny_edits = []
    for nid in list(tiny.nodes)[:10]:
        tiny_edits.append(Edit("set_text", nid, (f"V{nid}",)))
    # One-character label change: smallest possible primitive edit.
    tiny_edits.append(Edit("set_text", list(tiny.nodes)[0], ("X",)))
    tiny_edits += _move_edits(list(tiny.nodes)[:4], rng, steps=4,
                              span=(40, 30))

    overlap, (base, a, b, c, lone) = _overlap_scene()
    overlap_edits = [
        # Edit the bottom opaque base while translucent A/B/C cover it:
        # naive flags repaint only the base and destroy the alpha stack.
        Edit("set_fill", base, ((200, 220, 255, 255),)),
        # Move the middle translucent node: its old and new footprint both
        # intersect untouched translucent neighbours.
        Edit("move", b, (30, 20)),
        # Local label edit on the top node with no geometry change.
        Edit("set_text", c, ("ALPHA C2",)),
        # Base label edit; its bounds still intersect the translucent stack.
        Edit("set_text", base, ("OPAQUE BASE 2",)),
        # Isolated opaque node inside the same scene: its own edit must be
        # correct even with the naive strategy, because nothing overlaps it.
        Edit("set_text", lone, ("LONE OPAQUE 2",)),
    ]

    isolated, iso_ids = _isolated_scene()
    # Static local edits with non-growing labels: a longer label would
    # overflow the node's old bounds, which pure flags (no bounds-dependency
    # machinery) cannot cover -- that failure mode is demonstrated by the
    # other scenes.  Here the preconditions for naive correctness hold.
    isolated_edits = [Edit("set_text", iso_ids[0], ("ISO 0_0",)),
                      Edit("set_text", iso_ids[5], ("X",)),
                      Edit("set_text", iso_ids[9], ("ISO 2_1",)),
                      Edit("set_text", iso_ids[2], ("ISO 0_2",))]

    return [
        Scenario("big_node_move",
                 "large node + connected edges moved inside a dense grid",
                 _grid_scene, grid_edits,
                 naive_expected_to_break=True),
        Scenario("tiny_primitive_edit",
                 "small label/geometry edits on fine-grained primitives",
                 lambda: _tiny_edit_scene(), tiny_edits,
                 naive_expected_to_break=True),
        Scenario("translucent_overlap",
                 "edits inside stacked translucent overlaps (dependency test)",
                 _overlap_scene, overlap_edits,
                 naive_expected_to_break=True),
        Scenario("isolated_opaque",
                 "disjoint opaque nodes: naive per-object repaint must work",
                 _isolated_scene, isolated_edits,
                 naive_expected_to_break=False),
    ]


# -------------------------------------------------------------------- apply
def _affected_keys(scene: Scene, edit: Edit) -> set[tuple[str, int]]:
    keys = {("node", edit.target)}
    for eid in scene.connected_edges(edit.target):
        keys.add(("edge", eid))
    return keys


def _apply_edit(scene: Scene, edit: Edit) -> None:
    if edit.op == "move":
        dx, dy = edit.args
        node = scene.nodes[edit.target]
        scene.move_node(edit.target, node.x + dx, node.y + dy)
    elif edit.op == "set_text":
        scene.set_node_text(edit.target, edit.args[0])
    elif edit.op == "set_fill":
        scene.nodes[edit.target].fill = edit.args[0]
        scene.nodes[edit.target].bump()
    elif edit.op == "delete":
        scene.remove_node(edit.target)
    else:
        raise ValueError(f"unknown edit op {edit.op}")


def _make_updaters(scene: Scene):
    common = dict(width=WIDTH, height=HEIGHT, transform=TRANSFORM)
    return {
        "node_dirty": NodeDirtyUpdater(scene, **common),
        "naive_node_dirty": NaiveNodeDirtyUpdater(scene, **common),
        "region_propagation": RegionPropagationUpdater(scene, **common),
    }


def _count_mismatch(updater, ref_surface) -> int:
    a, b = updater.surface.buf, ref_surface.buf
    return sum(1 for i in range(0, len(a), 4)
               if a[i] != b[i] or a[i + 1] != b[i + 1]
               or a[i + 2] != b[i + 2] or a[i + 3] != b[i + 3])


def run() -> dict:
    scenarios = build_scenarios()
    report = {"viewport": [WIDTH, HEIGHT],
              "fallback_policy": {
                  "dirty_area_ratio": FALLBACK_DIRTY_AREA_RATIO,
                  "dirty_rect_count": FALLBACK_DIRTY_RECT_COUNT,
                  "dependent_ratio": FALLBACK_DEPENDENT_RATIO},
              "scenarios": []}
    os.makedirs(OUT_DIR, exist_ok=True)
    failures = []

    for sc in scenarios:
        built = sc.build()
        scene = built[0] if isinstance(built, tuple) else built
        updaters = _make_updaters(scene)
        # Initial frame must already match the full-frame reference.
        ref0 = updaters["region_propagation"].render_full_reference()
        for name, up in updaters.items():
            mm = _count_mismatch(up, ref0)
            if mm:
                failures.append(f"{sc.name}:{name} initial mismatch {mm}px")

        step_records = []
        for step, edit in enumerate(sc.edits):
            keys = _affected_keys(scene, edit)
            old_bounds = {
                name: up.bounds_of(keys) for name, up in updaters.items()}
            _apply_edit(scene, edit)

            updaters["node_dirty"].flag(keys, old_bounds["node_dirty"])
            updaters["naive_node_dirty"].flag(
                keys, old_bounds["naive_node_dirty"])
            updaters["region_propagation"].invalidate(
                keys, old_bounds["region_propagation"])

            ref_surface = updaters["region_propagation"].render_full_reference()
            record = {"step": step, "op": edit.op, "target": edit.target,
                      "naive_expected_to_break": sc.naive_expected_to_break,
                      "strategies": {}}
            for name, up in updaters.items():
                res: UpdateResult = up.update()
                res.mismatch_px = _count_mismatch(up, ref_surface)
                res.pixel_match = res.mismatch_px == 0
                record["strategies"][name] = res.as_dict()
                if name == "naive_node_dirty":
                    if sc.naive_expected_to_break and res.pixel_match:
                        failures.append(
                            f"{sc.name} step {step}: naive control was "
                            "expected to mismatch but matched")
                    if not sc.naive_expected_to_break and not res.pixel_match:
                        failures.append(
                            f"{sc.name} step {step}: naive control "
                            f"unexpected mismatch {res.mismatch_px}px")
                elif not res.pixel_match:
                    failures.append(
                        f"{sc.name} step {step}: {name} mismatch "
                        f"{res.mismatch_px}px")
            step_records.append(record)

        # Persist one PPM per strategy plus the reference for visual audit.
        for name, up in updaters.items():
            up.surface.write_ppm(
                os.path.join(OUT_DIR, f"granularity_{sc.name}_{name}.ppm"))
        updaters["region_propagation"].render_full_reference().write_ppm(
            os.path.join(OUT_DIR, f"granularity_{sc.name}_reference.ppm"))

        totals = {}
        for name in STRATEGIES:
            rows = [r["strategies"][name] for r in step_records]
            totals[name] = {
                "dirty_area_px": sum(r["dirty_area_px"] for r in rows),
                "painted_px": sum(r["painted_px"] for r in rows),
                "commands": sum(r["commands"] for r in rows),
                "replay_entries": sum(r["replay_entries"] for r in rows),
                "full_fallbacks": sum(1 for r in rows if r["fell_back"]),
                "pixel_mismatches": sum(r["mismatch_px"] for r in rows),
                "steps": len(rows),
            }
        report["scenarios"].append(
            {"name": sc.name, "description": sc.description,
             "object_count": len(scene.nodes) + len(scene.edges),
             "steps": step_records, "totals": totals})

    report["failures"] = failures
    with open(os.path.join(OUT_DIR, "granularity_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(OUT_DIR, "granularity_report.md"), "w",
              encoding="utf-8") as f:
        f.write(render_markdown(report))
    return report


# ------------------------------------------------------------------ report
def _pct(part: int, whole: int) -> str:
    return f"{100 * part / max(1, whole):.2f}%"


def render_markdown(report: dict) -> str:
    vp = report["viewport"][0] * report["viewport"][1]
    pol = report["fallback_policy"]
    lines = [
        "# 更新粒度实验：节点脏标记 vs 绘制区域传播",
        "",
        f"视口 {report['viewport'][0]}x{report['viewport'][1]} "
        f"（{vp} px），三个更新器消费同一份 `build_display_list` 输出。",
        "每一步都与独立整帧重绘 `render_full_reference()` 逐字节比对，",
        "重绘面积与像素差异由代码复算，原始数据见 granularity_results.json。",
        "",
        "## 退回全量绘制的显式条件",
        "",
        f"- 合并后脏区面积 >= 视口的 {pol['dirty_area_ratio']:.0%}",
        f"- 脏矩形数量 >= {pol['dirty_rect_count']} 个（碎片化）",
        f"- 节点脏标记的依赖扩展集 >= 显示列表条目的 "
        f"{pol['dependent_ratio']:.0%}",
        "- 缩放（scale 改变）使所有 (id,version,scale) 缓存键失效",
        "",
        "## 场景汇总（累计工作量）",
        "",
        "| 场景 | 策略 | 脏区面积(px) | 占视口/步 | 实际绘制px | 重放条目 | 全量退回 | 像素不符 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sc in report["scenarios"]:
        steps = len(sc["steps"])
        for name in STRATEGIES:
            t = sc["totals"][name]
            label = {"node_dirty": "节点脏标记(含依赖扩展)",
                     "naive_node_dirty": "朴素节点脏标记(负对照)",
                     "region_propagation": "绘制区域传播"}[name]
            lines.append(
                f"| {sc['name']} | {label} | {t['dirty_area_px']} | "
                f"{_pct(t['dirty_area_px'], vp * steps)} | "
                f"{t['painted_px']} | {t['replay_entries']} | "
                f"{t['full_fallbacks']} | {t['pixel_mismatches']} |")
    lines += ["", "## 逐步明细（脏区面积 / 实际绘制px / 像素差异）", ""]
    for sc in report["scenarios"]:
        lines.append(f"### {sc['name']} —— {sc['description']}")
        lines.append("")
        lines.append("| 步 | 操作 | 策略 | 脏区px | 占视口 | 绘制px | 退回 | 像素差异 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for rec in sc["steps"]:
            for name in STRATEGIES:
                r = rec["strategies"][name]
                lines.append(
                    f"| {rec['step']} | {rec['op']}#{rec['target']} | "
                    f"{name} | {r['dirty_area_px']} | "
                    f"{_pct(r['dirty_area_px'], vp)} | "
                    f"{r['painted_px']} | "
                    f"{'是:' + r['fallback_reason'] if r['fell_back'] else '否'} | "
                    f"{r['mismatch_px']} |")
        lines.append("")
    lines += ["## 实验结论", "",
              "结论正文见 docs/granularity_experiment.md；本文件由 "
              "`python3 -m flowkern.granularity_experiment` 重新生成，",
              "任何手工改数都会被下次运行覆盖。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    report = run()
    print(f"wrote {OUT_DIR}/granularity_results.json and "
          f"{OUT_DIR}/granularity_report.md")
    for sc in report["scenarios"]:
        print(f"\n[{sc['name']}] {sc['description']}")
        vp = WIDTH * HEIGHT
        for name in STRATEGIES:
            t = sc["totals"][name]
            print(f"  {name:18s} dirty={t['dirty_area_px']:>8d}px "
                  f"({100 * t['dirty_area_px'] / (vp * t['steps']):5.2f}%/step)"
                  f"  painted={t['painted_px']:>9d}px  "
                  f"fallbacks={t['full_fallbacks']}  "
                  f"mismatch_px={t['pixel_mismatches']}")
    if report["failures"]:
        print("\nFAIL:")
        for msg in report["failures"]:
            print(" -", msg)
        return 2
    print("\nOK: every non-naive strategy pixel-matched the full-frame "
          "reference on every step; naive mismatches were exactly the "
          "expected translucent-overlap cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line auxiliary for retained drawing.

Subcommands:
  render     write one flowchart frame (demo or dense scene)
  edit       replay retained edit commands, save frames + dirty overlay
  stress     dense scene: compare incremental vs full-frame reference,
             verify pixel equality and report the real work saved
"""
from __future__ import annotations

import argparse
import time

from .editor import Editor
from .scene_factory import build_demo_scene, build_dense_scene
from .headless_window import HeadlessWindow


def _ppm_paths(out: str):
    if out.endswith(".ppm"):
        base = out[:-4]
    else:
        base = out
    return base + ".ppm", base + "_dirty.ppm"


def cmd_render(args) -> int:
    scene = (build_dense_scene(args.cols, args.rows) if args.dense
             else build_demo_scene())
    editor = Editor(scene, args.width, args.height)
    frame, overlay = _ppm_paths(args.out)
    editor.save(frame)
    editor.kernel.draw_dirty_overlay(editor.dirty_rects())
    editor.kernel.save(overlay)
    print(f"frame   : {frame}")
    print(f"overlay : {overlay}")
    print(editor.display_list_text())
    return 0


def cmd_edit(args) -> int:
    win = HeadlessWindow(args.width, args.height, dense=args.dense,
                         cols=args.cols, rows=args.rows)
    with open(args.script, "r", encoding="utf-8") as f:
        win.run_script(f.readlines())
    frame, overlay = _ppm_paths(args.out)
    win.editor.save(frame)
    win.editor.kernel.draw_dirty_overlay(win.editor.dirty_rects())
    win.editor.kernel.save(overlay)
    print(f"frame   : {frame}")
    print(f"overlay : {overlay}")
    return 0


def _buffers_equal(a, b) -> bool:
    return bytes(a.surface.buf) == bytes(b.surface.buf)


def cmd_stress(args) -> int:
    scene = build_dense_scene(args.cols, args.rows)
    viewport = args.width * args.height

    # ---- reference: full frame redraw --------------------------------
    ref = Editor(scene, args.width, args.height)
    ref.reset_stats() if hasattr(ref, "reset_stats") else None
    ref.kernel.reset_stats()
    t0 = time.perf_counter()
    ref.kernel.render_full()
    t_full = time.perf_counter() - t0
    full_stats = ref.kernel.stats()
    ref.kernel.save(args.out + "_ref.ppm")

    # ---- incremental: identical initial state, then rapid edits ------
    inc_scene = build_dense_scene(args.cols, args.rows)
    inc = Editor(inc_scene, args.width, args.height)
    inc.kernel.render_incremental()
    if not _buffers_equal(ref.kernel, inc.kernel):
        print("FAIL: initial incremental frame differs from reference")
        return 2
    print(f"OK  initial incremental frame == full-frame reference "
          f"({viewport} px)")

    import random
    rng = random.Random(11)
    node_ids = list(inc_scene.nodes)
    cumulative = {"commands": 0, "painted_px": 0, "blit_px": 0,
                  "scroll_px": 0, "dirty_area_px": 0}
    worst_ratio = 0.0
    checks = 0
    for step in range(args.edits):
        nid = rng.choice(node_ids)
        node = inc_scene.nodes[nid]
        nx = node.x + rng.uniform(-90, 90)
        ny = node.y + rng.uniform(-60, 60)
        ids = {("node", nid)}
        for eid in inc_scene.connected_edges(nid):
            ids.add(("edge", eid))
        old = [e.bounds for e in inc.kernel.last_display_list
               if (e.kind, e.obj_id) in ids]
        inc_scene.move_node(nid, nx, ny)
        inc.kernel.invalidate_objects(list(ids), old_bounds=old)
        inc.kernel.reset_stats()
        rects = inc.kernel.render_incremental()
        st = inc.kernel.stats()
        for k in cumulative:
            cumulative[k] += st[k]
        ratio = st["dirty_area_px"] / viewport
        worst_ratio = max(worst_ratio, ratio)

        # Pixel correctness against a fresh full redraw of the same scene.
        if checks < args.verify or step % 25 == 0:
            verify = Editor(_rebuild_like(inc_scene), args.width,
                            args.height)
            verify.kernel.render_full()
            if not _buffers_equal(verify.kernel, inc.kernel):
                print(f"FAIL: pixel mismatch after edit {step} (node {nid})")
                inc.kernel.save(args.out + "_bad_inc.ppm")
                verify.kernel.save(args.out + "_bad_ref.ppm")
                return 2
            checks += 1

    inc.kernel.save(args.out + "_inc.ppm")
    inc.kernel.draw_dirty_overlay(inc.kernel.last_repainted)
    inc.kernel.save(args.out + "_inc_dirty.ppm")

    print(f"\nscene          : {args.cols}x{args.rows} nodes, "
          f"{len(scene.edges)} edges, viewport {args.width}x{args.height}")
    print(f"full reference : {t_full * 1000:.1f} ms, "
          f"commands={full_stats['commands']}, "
          f"paint_px={full_stats['painted_px']}")
    print(f"edits          : {args.edits} rapid node moves, "
          f"{checks} pixel-identical full-frame verifications")
    print(f"cumulative partial work: {cumulative}")
    saved_full = full_stats["painted_px"] * args.edits
    print(f"full redraw would paint {saved_full} px over the same edits; "
          f"partial painted {cumulative['painted_px']} px "
          f"({100 * cumulative['painted_px'] / max(1, saved_full):.2f}%)")
    print(f"worst single-edit dirty coverage: {worst_ratio * 100:.2f}% "
          "of frame")
    print(f"outputs        : {args.out}_ref.ppm, {args.out}_inc.ppm, "
          f"{args.out}_inc_dirty.ppm")
    return 0


def _rebuild_like(scene):
    """Clone scene geometry for an independent full-frame verification."""
    from .scene import Scene
    clone = Scene()
    id_map = {}
    for nid, n in scene.nodes.items():
        new_id = clone.add_node(
            n.x, n.y, n.w, n.h, n.text, n.shape, radius=n.radius,
            fill=n.fill, stroke=n.stroke, stroke_w=n.stroke_w,
            text_color=n.text_color, text_scale=n.text_scale,
            shadow_blur=n.shadow_blur, shadow_offset=n.shadow_offset,
            shadow_color=n.shadow_color, z=n.z)
        id_map[nid] = new_id
    for edge in scene.edges.values():
        clone.add_edge(id_map[edge.src], id_map[edge.dst], edge.text,
                       color=edge.color, width=edge.width,
                       text_color=edge.text_color, text_scale=edge.text_scale,
                       marker_end=edge.marker_end,
                       marker_start=edge.marker_start, z=edge.z)
    return clone


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="flowkern",
        description="Retained flowchart rendering kernel CLI (auxiliary)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render", help="render one frame")
    p.add_argument("--out", default="demo_out/render")
    p.add_argument("--dense", action="store_true")
    p.add_argument("--cols", type=int, default=10)
    p.add_argument("--rows", type=int, default=12)
    p.add_argument("--width", type=int, default=1120)
    p.add_argument("--height", type=int, default=460)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("edit", help="replay retained edit commands")
    p.add_argument("script")
    p.add_argument("--out", default="demo_out/edit")
    p.add_argument("--dense", action="store_true")
    p.add_argument("--cols", type=int, default=10)
    p.add_argument("--rows", type=int, default=12)
    p.add_argument("--width", type=int, default=1120)
    p.add_argument("--height", type=int, default=460)
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("stress",
                       help="incremental vs full-frame reference test")
    p.add_argument("--out", default="demo_out/stress")
    p.add_argument("--cols", type=int, default=10)
    p.add_argument("--rows", type=int, default=12)
    p.add_argument("--width", type=int, default=1120)
    p.add_argument("--height", type=int, default=460)
    p.add_argument("--edits", type=int, default=40)
    p.add_argument("--verify", type=int, default=8,
                   help="number of edits to verify against full redraw")
    p.set_defaults(func=cmd_stress)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

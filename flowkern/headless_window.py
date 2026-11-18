"""Headless retained-drawing vector editor window.

A real editor session with no display server: commands mutate the retained
scene, the kernel repaints only the invalidated region, and every step
reports the display list and dirty-region work.  The same command set also
drives the Tk view (see tk_window.py).
"""
from __future__ import annotations

import sys

from .editor import Editor, EditorError
from .scene_factory import build_demo_scene, build_dense_scene


HELP = """\
commands (all edits are retained; only invalid regions are repainted):
  add <x> <y> [text]              add node centred at device x,y
  edge <src_id> <dst_id> [text]   connect two nodes
  text <node|edge> <id> <string>  replace label
  move <id> <x> <y>               move node (edges follow, old area cleared)
  drag <id> <x1> <y1> <x2> <y2>   simulate a drag with begin/end events
  delete <node|edge> <id>         remove object and erase its old pixels
  pan <dx> <dy>                   translate the view (pixel scroll)
  zoom <factor>                   scale around the viewport centre
  hit <x> <y>                     hit-test a device point
  list                            show the display list
  dirty                           show current invalidated rectangles
  stats                           show real paint work counters
  save [path.ppm]                 write the current frame
  overlay [path.ppm]              save frame with last dirty rects marked
  reference                       full-frame reference redraw
  reset                           rebuild the demo scene
  dense [cols rows]               load the dense stress scene
  help / quit
"""


class HeadlessWindow:
    def __init__(self, width=1120, height=460, dense=False, cols=10, rows=12):
        self.width = width
        self.height = height
        self._build(dense, cols, rows)
        self._seq = 0

    def _build(self, dense, cols, rows):
        scene = (build_dense_scene(cols, rows) if dense
                 else build_demo_scene())
        self.editor = Editor(scene, self.width, self.height)

    def prompt(self) -> str:
        return "flowkern> "

    def handle(self, line: str) -> bool:
        parts = line.strip().split()
        if not parts:
            return True
        cmd, args = parts[0], parts[1:]
        try:
            if cmd in ("quit", "exit"):
                return False
            elif cmd == "help":
                print(HELP)
            elif cmd == "add":
                nid = self.editor.add_node(int(args[0]), int(args[1]),
                                           140, 56,
                                           " ".join(args[2:]) or "NEW NODE")
                print(f"node {nid} created")
            elif cmd == "edge":
                eid = self.editor.add_edge(int(args[0]), int(args[1]),
                                           " ".join(args[2:]))
                print(f"edge {eid} created")
            elif cmd == "text":
                kind, oid, text = args[0], int(args[1]), " ".join(args[2:])
                self.editor.set_text(kind, oid, text)
                print("text updated")
            elif cmd == "move":
                self._move(int(args[0]), float(args[1]), float(args[2]))
            elif cmd == "drag":
                self._drag(*(int(a) for a in args))
            elif cmd == "delete":
                self.editor.delete(args[0], int(args[1]))
                print("deleted")
            elif cmd == "pan":
                self.editor.pan(int(args[0]), int(args[1]))
            elif cmd == "zoom":
                self.editor.zoom(float(args[0]))
            elif cmd == "hit":
                hit = self.editor.hit_test(int(args[0]), int(args[1]))
                print("hit:", hit if hit else "none")
            elif cmd == "list":
                print(self.editor.display_list_text())
            elif cmd == "dirty":
                rects = self.editor.dirty_rects()
                print("invalidated rects:",
                      [(r.x0, r.y0, r.x1, r.y1) for r in rects],
                      "area:", sum(r.area for r in rects), "px")
            elif cmd == "stats":
                self._print_stats()
            elif cmd == "save":
                path = args[0] if args else self._auto("frame")
                self.editor.save(path)
                print("saved", path)
            elif cmd == "overlay":
                path = args[0] if args else self._auto("overlay")
                self._save_overlay(path)
            elif cmd == "reference":
                self.editor.full_redraw_reference()
                print("full-frame reference redraw complete")
            elif cmd == "reset":
                self._build(False, 10, 12)
                print("demo scene rebuilt")
            elif cmd == "dense":
                cols = int(args[0]) if len(args) > 0 else 10
                rows = int(args[1]) if len(args) > 1 else 12
                self._build(True, cols, rows)
                print(f"dense scene {cols}x{rows} loaded")
            else:
                print(f"unknown command: {cmd} (try 'help')")
                return True
        except (EditorError, ValueError, IndexError, KeyError) as exc:
            print(f"[retained-draw error] {exc} — {self.editor.message}")
        return True

    def _move(self, nid: int, lx: float, ly: float) -> None:
        old = self.editor.kernel.last_display_list
        ids = {("node", nid)}
        for eid in self.editor.scene.connected_edges(nid):
            ids.add(("edge", eid))
        old_bounds = [e.bounds for e in old if (e.kind, e.obj_id) in ids]
        self.editor.scene.move_node(nid, lx, ly)
        self.editor.kernel.invalidate_objects(list(ids),
                                              old_bounds=old_bounds)
        self.editor.kernel.render_incremental()
        self._print_step(f"move node {nid}")

    def _drag(self, nid, x1, y1, x2, y2) -> None:
        hit = self.editor.begin_drag(x1, y1)
        if hit is None or hit.obj_id != nid:
            print("[retained-draw error] drag start missed the node")
            return
        self.editor.drag_to(x2, y2)
        self.editor.end_drag()
        self._print_step(f"drag node {nid}")

    def _save_overlay(self, path: str) -> None:
        import copy
        from .raster import Surface
        k = self.editor.kernel
        snapshot = bytearray(k.surface.buf)
        k.draw_dirty_overlay(self.editor.dirty_rects())
        k.save(path)
        k.surface.buf = snapshot
        print("saved", path)

    def _print_step(self, label: str) -> None:
        self._seq += 1
        rects = self.editor.dirty_rects()
        st = self.editor.stats()
        print(f"[{self._seq}] {label}")
        print("    dirty:",
              [(r.x0, r.y0, r.x1, r.y1) for r in rects],
              f"area={st['dirty_area_px']}px "
              f"({100 * st['dirty_area_px'] / max(1, st['frame_area_px']):.1f}% "
              "of frame)")
        print(f"    work: commands={st['commands']} "
              f"paint_px={st['painted_px']} blit_px={st['blit_px']} "
              f"scroll_px={st['scroll_px']} layers={st['cached_layers']}")

    def _print_stats(self) -> None:
        st = self.editor.stats()
        for k, v in st.items():
            print(f"  {k:18s} {v}")

    def _auto(self, tag: str) -> str:
        self._seq += 1
        return f"demo_out/{tag}_{self._seq:03d}.ppm"

    def run_script(self, lines) -> list[str]:
        out = []
        old = sys.stdout
        class _Tee:
            def write(self, s):
                out.append(s)
                old.write(s)
            def flush(self):
                old.flush()
        sys.stdout = _Tee()
        try:
            for line in lines:
                if line.strip() and not line.lstrip().startswith("#"):
                    print(self.prompt() + line)
                    if not self.handle(line):
                        break
        finally:
            sys.stdout = old
        return out

    def interact(self) -> None:
        print(HELP)
        print(f"viewport {self.width}x{self.height}; demo scene loaded")
        while True:
            try:
                line = input(self.prompt())
            except EOFError:
                break
            if not self.handle(line):
                break


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Headless retained-mode flowchart editor window")
    ap.add_argument("--width", type=int, default=1120)
    ap.add_argument("--height", type=int, default=460)
    ap.add_argument("--dense", action="store_true")
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--rows", type=int, default=12)
    ap.add_argument("--script", type=str,
                    help="run command lines from a file then exit")
    args = ap.parse_args(argv)
    win = HeadlessWindow(args.width, args.height, args.dense,
                         args.cols, args.rows)
    if args.script:
        with open(args.script, "r", encoding="utf-8") as f:
            win.run_script(f.readlines())
    else:
        win.interact()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

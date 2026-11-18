"""Tk retained-drawing vector editor window.

Lazily imports Tk so the package works on headless machines; this view is
a thin shell over Editor, which contains all retained-mode logic.  The
left side shows the flowchart with the last invalidated region outlined,
the right side shows the display list, dirty rectangles and work counters,
and editing errors appear in a status line plus a dialog.
"""
from __future__ import annotations

from .editor import Editor, EditorError
from .scene_factory import build_demo_scene


def _ppm_photo(surface, tk):
    import base64
    data = bytearray(f"P6 {surface.w} {surface.h} 255 ".encode())
    b = surface.buf
    for i in range(0, len(b), 4):
        data += bytes((b[i], b[i + 1], b[i + 2]))
    return tk.call("image", "create", "photo", "-data",
                   base64.b64encode(bytes(data)), "-format", "PPM")


class TkWindow:
    def __init__(self, width: int = 1120, height: int = 520):
        import tkinter as tk
        self.tk = tk
        self.width = width
        self.height = height
        scene = build_demo_scene()
        self.editor = Editor(scene, width, height)

        self.root = tk.Tk()
        self.root.title("flowkern retained vector editor")
        self.root.configure(bg="#20242c")

        left = tk.Frame(self.root, bg="#20242c")
        left.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        right = tk.Frame(self.root, bg="#20242c")
        right.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(left, width=width, height=height,
                                highlightthickness=0, bg="#f5f6fa")
        self.canvas.pack()
        self._image_id = None

        bar = tk.Frame(left, bg="#20242c")
        bar.pack(fill="x", pady=(6, 0))
        tk.Button(bar, text="full reference",
                  command=self._full_reference).pack(side="left")
        tk.Button(bar, text="overlay dirty",
                  command=self._toggle_overlay).pack(side="left", padx=4)
        tk.Button(bar, text="add node", command=self._add_node).pack(
            side="left")
        tk.Button(bar, text="delete hit",
                  command=self._delete_hit).pack(side="left", padx=4)

        tk.Label(right, text="DISPLAY LIST", fg="#cfe0ff",
                 bg="#20242c").pack(anchor="w")
        self.list_box = tk.Text(right, width=58, height=22,
                                bg="#14181f", fg="#cfe0ff",
                                font=("Courier", 9))
        self.list_box.pack(fill="both", expand=True)

        tk.Label(right, text="INVALIDATED REGIONS / WORK", fg="#ffd0d0",
                 bg="#20242c").pack(anchor="w", pady=(8, 0))
        self.info_box = tk.Text(right, width=58, height=10,
                                bg="#14181f", fg="#b7e8c0",
                                font=("Courier", 9))
        self.info_box.pack(fill="both", expand=True)

        self.status = tk.StringVar(value="ready")
        tk.Label(self.root, textvariable=self.status, anchor="w",
                 fg="#ffd28a", bg="#20242c").grid(
            row=1, column=0, columnspan=2, sticky="we")

        self.show_overlay = False
        self._panning = False
        self._last = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_move)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Key>", self._on_key)
        self.refresh()

    # ------------------------------------------------------------ rendering
    def refresh(self) -> None:
        k = self.editor.kernel
        if self.show_overlay and self.editor.dirty_rects():
            snapshot = bytearray(k.surface.buf)
            k.draw_dirty_overlay(self.editor.dirty_rects())
            photo = _ppm_photo(k.surface, self.tk)
            k.surface.buf = snapshot
        else:
            photo = _ppm_photo(k.surface, self.tk)
        if self._image_id is None:
            self._image_id = self.canvas.create_image(
                0, 0, anchor="nw", image=photo)
        else:
            self.canvas.itemconfig(self._image_id, image=photo)
        self._photo = photo  # keep reference

        self.list_box.delete("1.0", "end")
        self.list_box.insert("end", self.editor.display_list_text())
        rects = self.editor.dirty_rects()
        st = self.editor.stats()
        lines = [
            "dirty rects:",
            *[f"  ({r.x0},{r.y0})-({r.x1},{r.y1}) {r.width}x{r.height}"
              for r in rects],
            f"dirty area : {st['dirty_area_px']} px "
            f"({100 * st['dirty_area_px'] / max(1, st['frame_area_px']):.2f}% "
            "of frame)",
            f"commands   : {st['commands']}",
            f"painted px : {st['painted_px']} "
            f"(fast={st['fast_fill_px']}, blend={st['blend_px']}, "
            f"text={st['text_px']})",
            f"blit px    : {st['blit_px']}",
            f"scroll px  : {st['scroll_px']}",
            f"cached     : {st['cached_layers']} object layers",
        ]
        self.info_box.delete("1.0", "end")
        self.info_box.insert("end", "\n".join(lines))
        self.status.set(self.editor.message)

    # -------------------------------------------------------------- events
    def _guard(self, fn):
        try:
            fn()
        except EditorError as exc:
            self.status.set(f"error: {exc}")
            self.tk.messagebox.showerror("retained draw error", str(exc))
        self.refresh()

    def _on_press(self, ev):
        self._guard(lambda: self.editor.begin_drag(ev.x, ev.y))

    def _on_drag(self, ev):
        if self.editor._drag_id is not None:
            self._guard(lambda: self.editor.drag_to(ev.x, ev.y))

    def _on_release(self, ev):
        self.editor.end_drag()
        self.refresh()

    def _on_pan_start(self, ev):
        self._panning = True
        self._last = (ev.x, ev.y)

    def _on_pan_move(self, ev):
        if not self._panning:
            return
        dx, dy = ev.x - self._last[0], ev.y - self._last[1]
        self._last = (ev.x, ev.y)
        self._guard(lambda: self.editor.pan(dx, dy))

    def _on_wheel(self, ev):
        factor = 1.15 if ev.delta > 0 else 1 / 1.15
        self._guard(lambda: self.editor.zoom(factor, (ev.x, ev.y)))

    def _on_key(self, ev):
        if ev.char == "r":
            self._guard(self.editor.full_redraw_reference)
        elif ev.char == "o":
            self.show_overlay = not self.show_overlay
            self.refresh()

    def _full_reference(self):
        self.editor.full_redraw_reference()

    def _toggle_overlay(self):
        self.show_overlay = not self.show_overlay

    def _add_node(self):
        self._guard(lambda: self.editor.add_node(
            self.width // 2, self.height // 2, 150, 60, "NEW NODE"))

    def _delete_hit(self):
        def fn():
            hit = self.editor.hit_test(self.width // 2, self.height // 2)
            if hit is None:
                raise EditorError("no object under viewport centre")
            self.editor.delete(hit.kind, hit.obj_id)
        self._guard(fn)

    def run(self):
        self.root.mainloop()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Tk retained-mode flowchart editor window")
    ap.add_argument("--width", type=int, default=1120)
    ap.add_argument("--height", type=int, default=520)
    args = ap.parse_args(argv)
    TkWindow(args.width, args.height).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Display list compiler and its explicit command records.

A display list entry is one scene object plus the ordered paint commands it
needs in *device* coordinates for the current view transform.  Commands
carry a semantic kind so the UI can show the list; execution stays in one
global (z, id) order and is never re-sorted by colour.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Rect, Transform
from . import font as bmfont
from .raster import line_quad


@dataclass
class PaintCommand:
    kind: str                 # shadow|fill|stroke|line|marker|text
    params: dict
    color: tuple


@dataclass
class DisplayEntry:
    kind: str                 # node | edge
    obj_id: int
    version: int
    bounds: Rect
    commands: list[PaintCommand] = field(default_factory=list)
    label: str = ""


# --------------------------------------------------------------- primitives
def _diamond_points(cx, cy, hw, hh):
    return [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]


def _arrow_head(x, y, angle, size):
    a1 = angle + math.radians(155)
    a2 = angle - math.radians(155)
    return [(x, y),
            (x + size * math.cos(a1), y + size * math.sin(a1)),
            (x + size * math.cos(a2), y + size * math.sin(a2))]


def _marker_circle(x, y, radius):
    steps = 12
    return [(x + radius * math.cos(2 * math.pi * i / steps),
             y + radius * math.sin(2 * math.pi * i / steps))
            for i in range(steps)]


def _text_origin(x, y, text, scale, align=("center", "center")):
    tw, th = bmfont.text_size(text, scale)
    ox = x - tw / 2 if align[0] == "center" else x
    oy = y - th / 2 if align[1] == "center" else y
    return ox, oy, tw, th


# --------------------------------------------------------------- node entry
def build_node_entry(node, tf: Transform) -> DisplayEntry:
    s = tf.scale
    x0 = tf.to_device_x(node.x)
    y0 = tf.to_device_y(node.y)
    x1 = tf.to_device_x(node.x + node.w)
    y1 = tf.to_device_y(node.y + node.h)
    sw = node.stroke_w * s
    r = max(0.0, node.radius * s)
    cmds: list[PaintCommand] = []

    # Shadow must paint before the body; its blur pad is part of the bounds.
    pad = node.shadow_blur
    sox, soy = node.shadow_offset[0] * s, node.shadow_offset[1] * s
    cmds.append(PaintCommand("shadow", {
        "shape": node.shape, "x0": x0 + sox, "y0": y0 + soy,
        "x1": x1 + sox, "y1": y1 + soy, "radius": r, "blur": pad,
    }, node.shadow_color))

    cmds.append(PaintCommand("fill", {
        "shape": node.shape, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "radius": r,
    }, node.fill))
    cmds.append(PaintCommand("stroke", {
        "shape": node.shape, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "radius": r, "stroke_w": sw,
    }, node.stroke))

    # Text can be wider than the body; measure it so bounds include overflow.
    if node.text:
        tscale = node.text_scale * s
        tx, ty, tw, th = _text_origin((x0 + x1) / 2, (y0 + y1) / 2,
                                      node.text, tscale)
        # Integer pen origin: guarantees identical rasterisation in frame
        # and cached-layer coordinate spaces.
        tx, ty = math.floor(tx), math.floor(ty)
        cmds.append(PaintCommand("text", {
            "x": tx, "y": ty, "text": node.text, "scale": tscale,
        }, node.text_color))
        tb = Rect.from_ltrb(tx, ty, tx + tw, ty + th)
    else:
        tb = Rect(0, 0, 0, 0)

    body = Rect.from_ltrb(x0, y0, x1, y1).inflated(
        math.ceil(sw / 2) + 1, math.ceil(sw / 2) + 1)
    sh = Rect.from_ltrb(x0 + sox, y0 + soy, x1 + sox, y1 + soy).inflated(
        pad, pad)
    bounds = body.union(sh).union(tb)
    return DisplayEntry("node", node.id, node.version, bounds, cmds,
                        f"N{node.id}:{node.text!r}")


# --------------------------------------------------------------- edge entry
def _node_anchor(node, tf: Transform):
    return (tf.to_device_x(node.cx), tf.to_device_y(node.cy))


def build_edge_entry(edge, src_node, dst_node, tf: Transform) -> DisplayEntry:
    x0, y0 = _node_anchor(src_node, tf)
    x1, y1 = _node_anchor(dst_node, tf)
    s = tf.scale
    w = edge.width * s
    cmds: list[PaintCommand] = []
    angle = math.atan2(y1 - y0, x1 - x0)
    marker_size = max(6.0, 4.0 * s + w * 2)

    cmds.append(PaintCommand("line", {
        "x0": x0, "y0": y0, "x1": x1, "y1": y1, "width": w,
    }, edge.color))

    if edge.marker_end in ("arrow", "arrow_both"):
        cmds.append(PaintCommand("marker", {
            "polygon": _arrow_head(x1, y1, angle, marker_size),
        }, edge.color))
    if edge.marker_start in ("arrow", "arrow_both"):
        cmds.append(PaintCommand("marker", {
            "polygon": _arrow_head(x0, y0, angle + math.pi, marker_size),
        }, edge.color))
    if edge.marker_end == "circle":
        cmds.append(PaintCommand("marker", {
            "polygon": _marker_circle(x1, y1, marker_size * 0.6),
        }, edge.color))

    bounds = Rect.from_ltrb(min(x0, x1), min(y0, y1),
                            max(x0, x1), max(y0, y1))
    bounds = bounds.inflated(math.ceil(w / 2) + marker_size + 1)

    if edge.text:
        tscale = edge.text_scale * s
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        tx, ty, tw, th = _text_origin(mx, my, edge.text, tscale)
        tx, ty = math.floor(tx), math.floor(ty)
        pad = math.ceil(max(2.0, 2 * s))
        tb = Rect.from_ltrb(tx - pad, ty - pad, tx + tw + pad,
                            ty + th + pad)
        cmds.append(PaintCommand("text_bg", {
            "rect": tb,
        }, (255, 255, 255, 200)))
        cmds.append(PaintCommand("text", {
            "x": tx, "y": ty, "text": edge.text, "scale": tscale,
        }, edge.text_color))
        bounds = bounds.union(tb)

    return DisplayEntry("edge", edge.id, edge.version, bounds, cmds,
                        f"E{edge.id}:{src_node.id}->{dst_node.id}")


# ----------------------------------------------------------------- compiler
def build_display_list(scene, tf: Transform) -> list[DisplayEntry]:
    entries: list[DisplayEntry] = []
    for kind, oid in scene.display_order():
        if kind == "node":
            entries.append(build_node_entry(scene.nodes[oid], tf))
        else:
            edge = scene.edges[oid]
            entries.append(build_edge_entry(
                edge, scene.nodes[edge.src], scene.nodes[edge.dst], tf))
    return entries


def execute_command(surface, cmd: PaintCommand) -> None:
    p = cmd.params
    if cmd.kind == "shadow":
        surface.draw_shadow(p, cmd.color)
    elif cmd.kind == "fill":
        surface.draw_shape(p, cmd.color, stroke=False)
    elif cmd.kind == "stroke":
        surface.draw_shape(p, cmd.color, stroke=True,
                           stroke_w=p["stroke_w"])
    elif cmd.kind == "line":
        surface.stroke_line(p["x0"], p["y0"], p["x1"], p["y1"],
                            p["width"], cmd.color)
    elif cmd.kind == "marker":
        surface.fill_polygon(p["polygon"], cmd.color)
    elif cmd.kind == "text":
        surface.draw_text(p["x"], p["y"], p["text"], p["scale"], cmd.color)
    elif cmd.kind == "text_bg":
        r = p["rect"]
        surface.fill_rect(r.x0, r.y0, r.x1, r.y1, cmd.color)

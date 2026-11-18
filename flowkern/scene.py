"""Retained scene graph: stable object identity plus per-object versions.

Objects are addressed by stable integer ids.  Every geometry/style change
bumps an object version; moving a node also bumps every connected edge, so
the renderer can keep (id, version, scale) keyed caches and still never
paint stale geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .color import rgba

NODE_SHAPES = ("rect", "round", "diamond", "ellipse")
EDGE_MARKERS = ("none", "arrow", "arrow_both", "circle")


@dataclass
class Node:
    id: int
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    shape: str = "round"
    radius: float = 12.0
    fill: tuple = (214, 232, 250, 255)
    stroke: tuple = (34, 88, 160, 255)
    stroke_w: float = 2.0
    text_color: tuple = (30, 40, 60, 255)
    text_scale: float = 1.6
    shadow_blur: int = 5
    shadow_offset: tuple = (3.0, 4.0)
    shadow_color: tuple = (0, 0, 0, 90)
    z: int = 2
    version: int = 1

    def bump(self) -> None:
        self.version += 1

    def contains(self, lx: float, ly: float) -> bool:
        return self.x <= lx <= self.x + self.w and self.y <= ly <= self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Edge:
    id: int
    src: int
    dst: int
    text: str = ""
    color: tuple = (70, 80, 100, 255)
    width: float = 2.0
    text_color: tuple = (50, 55, 70, 255)
    text_scale: float = 1.4
    marker_end: str = "arrow"
    marker_start: str = "none"
    z: int = 1
    version: int = 1

    def bump(self) -> None:
        self.version += 1


class Scene:
    def __init__(self):
        self.nodes: dict[int, Node] = {}
        self.edges: dict[int, Edge] = {}
        self._next_id = 1

    def add_node(self, x, y, w, h, text="", shape="round", **kw) -> int:
        nid = self._next_id
        self._next_id += 1
        node = Node(id=nid, x=float(x), y=float(y), w=float(w), h=float(h),
                    text=text, shape=shape, **kw)
        self.nodes[nid] = node
        return nid

    def add_edge(self, src: int, dst: int, text="", **kw) -> int:
        if src not in self.nodes or dst not in self.nodes:
            raise KeyError(f"edge endpoints must exist: {src}->{dst}")
        eid = self._next_id
        self._next_id += 1
        self.edges[eid] = Edge(id=eid, src=src, dst=dst, text=text, **kw)
        return eid

    def remove_node(self, nid: int) -> None:
        if nid not in self.nodes:
            raise KeyError(f"unknown node {nid}")
        doomed = [eid for eid, e in self.edges.items()
                  if e.src == nid or e.dst == nid]
        for eid in doomed:
            del self.edges[eid]
        del self.nodes[nid]

    def remove_edge(self, eid: int) -> None:
        if eid not in self.edges:
            raise KeyError(f"unknown edge {eid}")
        del self.edges[eid]

    def move_node(self, nid: int, nx: float, ny: float) -> None:
        node = self.nodes[nid]
        node.x, node.y = float(nx), float(ny)
        node.bump()
        for edge in self.edges.values():
            if edge.src == nid or edge.dst == nid:
                edge.bump()

    def set_node_text(self, nid: int, text: str) -> None:
        node = self.nodes[nid]
        node.text = text
        node.bump()

    def set_z(self, oid: int, z: int) -> None:
        obj = self.nodes.get(oid) or self.edges.get(oid)
        if obj is None:
            raise KeyError(f"unknown object {oid}")
        obj.z = z
        obj.bump()

    def connected_edges(self, nid: int) -> list[int]:
        return [eid for eid, e in self.edges.items()
                if e.src == nid or e.dst == nid]

    def display_order(self) -> list[tuple[str, int]]:
        """Paint order keyed on (z, insertion order) only - never colour."""
        entries = [("edge", eid) for eid in self.edges]
        entries += [("node", nid) for nid in self.nodes]
        entries.sort(key=lambda t:
                     (self.edges[t[1]].z if t[0] == "edge"
                      else self.nodes[t[1]].z, t[1]))
        return entries

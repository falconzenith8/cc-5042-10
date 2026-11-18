"""Sample scenes: a readable demo flowchart and a dense stress layout."""
from __future__ import annotations

import random

from .scene import Scene


def build_demo_scene() -> Scene:
    scene = Scene()
    n1 = scene.add_node(40, 40, 170, 64, "START REQUEST", shape="round",
                        fill=(198, 230, 200, 255), stroke=(40, 130, 60, 255))
    n2 = scene.add_node(300, 40, 180, 64, "VALIDATE TOKEN",
                        fill=(214, 232, 250, 255))
    n3 = scene.add_node(560, 30, 180, 84, "TOKEN OK?", shape="diamond",
                        fill=(255, 236, 190, 255), stroke=(190, 130, 30, 255))
    n4 = scene.add_node(560, 180, 200, 70, "LOAD CONTEXT",
                        fill=(214, 232, 250, 255))
    n5 = scene.add_node(840, 30, 200, 64, "REJECT 401",
                        fill=(250, 205, 205, 255), stroke=(170, 50, 50, 255))
    n6 = scene.add_node(840, 180, 200, 70, "RUN PIPELINE",
                        fill=(225, 214, 245, 255), stroke=(100, 70, 170, 255))
    n7 = scene.add_node(560, 310, 200, 70, "PERSIST RESULT",
                        fill=(214, 232, 250, 255))
    n8 = scene.add_node(300, 310, 180, 70, "RENDER RESPONSE",
                        fill=(214, 232, 250, 255))
    n9 = scene.add_node(60, 310, 170, 70, "DONE", shape="ellipse",
                        fill=(198, 230, 200, 255), stroke=(40, 130, 60, 255))
    # Two translucent overlapping nodes to exercise alpha compositing.
    n10 = scene.add_node(420, 250, 260, 40, "SHARED CACHE LAYER",
                         fill=(80, 160, 255, 110),
                         stroke=(30, 90, 200, 180))
    n11 = scene.add_node(500, 270, 240, 40, "AUDIT TRAIL",
                         fill=(255, 120, 200, 110),
                         stroke=(200, 40, 130, 180))

    scene.add_edge(n1, n2)
    scene.add_edge(n2, n3)
    scene.add_edge(n3, n5, text="NO")
    scene.add_edge(n3, n4, text="YES")
    scene.add_edge(n4, n6, text="DISPATCH")
    scene.add_edge(n6, n7)
    scene.add_edge(n7, n8)
    scene.add_edge(n8, n9)
    scene.add_edge(n4, n7, text="RETRY", marker_start="arrow")
    # Long cross-screen edge.
    scene.add_edge(n9, n5, text="ESCALATE", marker_end="circle",
                   color=(150, 60, 60, 200))
    return scene


def build_dense_scene(cols: int = 10, rows: int = 12,
                      spacing_x: float = 150,
                      spacing_y: float = 100,
                      seed: int = 7) -> Scene:
    rng = random.Random(seed)
    scene = Scene()
    ids: list[list[int]] = []
    shapes = ("rect", "round", "diamond", "ellipse")
    fills = [
        (214, 232, 250, 255),
        (198, 230, 200, 255),
        (255, 236, 190, 255),
        (225, 214, 245, 255),
        (250, 205, 205, 255),
        (120, 170, 255, 120),
    ]
    for r in range(rows):
        row = []
        for c in range(cols):
            w = rng.uniform(80, 130)
            h = rng.uniform(46, 72)
            nid = scene.add_node(
                c * spacing_x + rng.uniform(0, 20),
                r * spacing_y + rng.uniform(0, 16),
                w, h, f"N{r}_{c}",
                shape=shapes[(r + c) % len(shapes)],
                fill=rng.choice(fills),
                shadow_blur=rng.choice((3, 5, 7)))
            row.append(nid)
        ids.append(row)
    # Horizontal, vertical and long diagonal edges across the grid.
    for r in range(rows):
        for c in range(cols - 1):
            if rng.random() < 0.75:
                scene.add_edge(ids[r][c], ids[r][c + 1],
                               marker_end="arrow" if rng.random() < 0.6
                               else "none")
    for r in range(rows - 1):
        for c in range(cols):
            if rng.random() < 0.5:
                scene.add_edge(ids[r][c], ids[r + 1][c])
    for r in range(0, rows - 2, 2):
        scene.add_edge(ids[r][0], ids[r + 2][cols - 1], text="LONG",
                       color=(150, 60, 60, 200))
    return scene

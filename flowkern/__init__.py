"""flowkern: a from-scratch retained flowchart rendering kernel."""
from .geometry import Rect, Transform, merge_rects
from .raster import Surface, AlphaMask, WorkStats
from .scene import Scene, Node, Edge
from .displaylist import build_display_list, DisplayEntry, PaintCommand
from .renderer import RenderKernel
from .updaters import (NodeDirtyUpdater, NaiveNodeDirtyUpdater,
                       RegionPropagationUpdater, UpdateResult)
from .editor import Editor, EditorError, HitResult
from .scene_factory import build_demo_scene, build_dense_scene

__all__ = [
    "Rect", "Transform", "merge_rects", "Surface", "AlphaMask",
    "WorkStats", "Scene", "Node", "Edge", "build_display_list",
    "DisplayEntry", "PaintCommand", "RenderKernel", "Editor",
    "EditorError", "HitResult", "build_demo_scene", "build_dense_scene",
    "NodeDirtyUpdater", "NaiveNodeDirtyUpdater", "RegionPropagationUpdater",
    "UpdateResult",
]

"""Update-granularity prototypes driving one shared display list.

Two invalidation strategies sit behind the same interface so they can be
driven by identical edit scripts and compared pixel-for-pixel:

* :class:`NodeDirtyUpdater` -- per-node dirty flags.  Only flagged objects
  are repainted, which is cheap for local edits but cannot, on its own,
  reconstruct pixels belonging to *other* translucent objects that overlap
  a dirty object.  It therefore carries an explicit dependency-expansion
  rule and conservative full-frame fallbacks (see module constants).

* :class:`NaiveNodeDirtyUpdater` -- the same flags without dependency
  expansion.  It exists only as the experiment's negative control: it is
  the smallest possible repaint and stays correct for opaque isolated
  objects, but produces visibly wrong pixels under translucent overlap.

* :class:`RegionPropagationUpdater` -- paint-region propagation.  Old and
  new device bounds of every edited object are unioned into dirty rects;
  each rect is erased to the background and the *whole* display-list paint
  order is replayed clipped to that rect, so translucent neighbours are
  recomposed automatically and the result is identical to a full frame.

Both real updaters consume ``build_display_list`` output verbatim --
neither introduces its own command set or paint order.
"""
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect, Transform, merge_rects
from .raster import Surface
from .displaylist import build_display_list, execute_command


# Explicit fallback thresholds.  They are module-level named constants (not
# magic defaults) and every fallback is counted in UpdateResult.fell_back so
# an experiment report can never hide a full redraw behind ordinary numbers.
FALLBACK_DIRTY_AREA_RATIO = 0.60   # merged dirty region >= 60% of viewport
FALLBACK_DIRTY_RECT_COUNT = 12     # fragmented into this many rects or more
FALLBACK_DEPENDENT_RATIO = 0.50    # >= half the objects are dependency-hit


@dataclass
class UpdateResult:
    strategy: str
    rects: list[Rect]
    dirty_area_px: int
    painted_px: int
    commands: int
    clip_pushes: int
    fell_back: bool
    fallback_reason: str
    replay_entries: int          # display-list entries whose commands ran
    scanned_entries: int         # entries inspected for intersection
    pixel_match: bool | None = None  # filled in by the experiment harness
    mismatch_px: int = 0

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "dirty_rects": len(self.rects),
            "dirty_area_px": self.dirty_area_px,
            "painted_px": self.painted_px,
            "commands": self.commands,
            "clip_pushes": self.clip_pushes,
            "fell_back": self.fell_back,
            "fallback_reason": self.fallback_reason,
            "replay_entries": self.replay_entries,
            "scanned_entries": self.scanned_entries,
            "pixel_match": self.pixel_match,
            "mismatch_px": self.mismatch_px,
        }


class _BaseUpdater:
    """Common surface/display-list machinery for both strategies."""

    name = "base"

    def __init__(self, scene, width: int, height: int,
                 bg=(245, 246, 250, 255), transform=None):
        self.scene = scene
        self.w = width
        self.h = height
        self.bg = bg
        self._transform = transform or Transform(1.0, 24.0, 24.0)
        self.surface = Surface(width, height, bg)
        self.last_entries: list = []
        # Render the initial frame in full once; afterwards every edit goes
        # through the strategy-specific update path.
        self._paint_full_initial()

    # --------------------------------------------------------- shared ops
    def _viewport(self) -> Rect:
        return Rect(0, 0, self.w, self.h)

    def _build_entries(self) -> list:
        return build_display_list(self.scene, self._transform)

    def _paint_full_initial(self) -> None:
        entries = self._build_entries()
        self.last_entries = entries
        self.surface.clear(self.bg)
        for entry in entries:
            for cmd in entry.commands:
                execute_command(self.surface, cmd)

    def _full_repaint(self, reason: str) -> UpdateResult:
        self.surface.reset_stats()
        entries = self._build_entries()
        self.last_entries = entries
        self.surface.clear(self.bg)
        for entry in entries:
            for cmd in entry.commands:
                execute_command(self.surface, cmd)
        st = self.surface.stats
        vp = self._viewport()
        return UpdateResult(
            strategy=self.name, rects=[vp], dirty_area_px=vp.area,
            painted_px=st.painted_px, commands=st.commands,
            clip_pushes=st.clip_pushes, fell_back=True,
            fallback_reason=reason, replay_entries=len(entries),
            scanned_entries=len(entries))

    def _result(self, rects, fell_back, reason, replay, scanned) -> UpdateResult:
        st = self.surface.stats
        return UpdateResult(
            strategy=self.name, rects=rects,
            dirty_area_px=sum(r.area for r in rects),
            painted_px=st.painted_px, commands=st.commands,
            clip_pushes=st.clip_pushes, fell_back=fell_back,
            fallback_reason=reason, replay_entries=replay,
            scanned_entries=scanned)

    def render_full_reference(self) -> Surface:
        """Independent full-frame surface of the current scene."""
        ref = Surface(self.w, self.h, self.bg)
        for entry in self._build_entries():
            for cmd in entry.commands:
                execute_command(ref, cmd)
        return ref

    def bounds_of(self, keys) -> list[Rect]:
        wanted = set(keys)
        return [e.bounds for e in self.last_entries
                if (e.kind, e.obj_id) in wanted]


class NodeDirtyUpdater(_BaseUpdater):
    """Per-node dirty flag strategy, including its dependency model.

    The naive reading of this strategy repaints exactly the flagged
    objects.  That is correct while every flagged object is opaque and
    isolated, but a flagged object underneath an untouched translucent
    neighbour cannot be rebuilt without re-running the neighbour too:
    alpha compositing is order-sensitive and not associative.  We therefore
    expand the dirty set with every *living* entry whose paint bounds
    overlap a flagged entry and whose paint order puts it above at least
    one flagged entry, and we also keep old-bounds rectangles so moved and
    deleted objects leave no residue.
    """

    name = "node_dirty"

    def __init__(self, scene, width: int, height: int, **kw):
        super().__init__(scene, width, height, **kw)
        self.flagged: set[tuple[str, int]] = set()
        self.old_bounds: list[Rect] = []

    # ------------------------------------------------------ flag protocol
    def flag(self, keys, old_bounds=None) -> None:
        self.flagged.update(keys)
        if old_bounds:
            self.old_bounds.extend(old_bounds)

    def update(self) -> UpdateResult:
        if not self.flagged and not self.old_bounds:
            return self._result([], False, "", 0, 0)

        entries = self._build_entries()
        self.last_entries = entries
        by_key = {(e.kind, e.obj_id): i for i, e in enumerate(entries)}
        flagged_idx = [by_key[k] for k in self.flagged if k in by_key]

        # Old bounds belong to objects that may have moved or been deleted;
        # they must be erased even though no current entry covers them.
        residue = merge_rects(self.old_bounds, self._viewport())
        dirty_keys = set(self.flagged)

        # Dependency expansion: any entry overlapping a flagged entry and
        # painted at or above it must be repainted together so translucent
        # overlaps keep the exact source-over order of a full frame.
        expanded: set[int] = set(flagged_idx)
        floor = min(flagged_idx, default=0)
        for i in flagged_idx:
            fi = entries[i]
            for j, ej in enumerate(entries):
                if j == i or j in expanded or j < floor:
                    continue
                if ej.bounds.intersects(fi.bounds):
                    expanded.add(j)
                    dirty_keys.add((ej.kind, ej.obj_id))
        # Deleted/moved residue rectangles also drag in every overlapping
        # living entry, the same way a propagated region would.
        for r in residue:
            for j, ej in enumerate(entries):
                if ej.bounds.intersects(r):
                    expanded.add(j)
                    dirty_keys.add((ej.kind, ej.obj_id))

        if entries and len(expanded) >= FALLBACK_DEPENDENT_RATIO * len(entries):
            self._clear_flags()
            return self._full_repaint(
                f"dependency set {len(expanded)}/{len(entries)} >= "
                f"{FALLBACK_DEPENDENT_RATIO:.0%} of display list")

        # Repaint region = union of repainted entry bounds plus residue.
        rects = list(residue)
        for j in expanded:
            rects.append(entries[j].bounds)
        rects = merge_rects(rects, self._viewport())

        viewport_area = self.w * self.h
        if sum(r.area for r in rects) >= FALLBACK_DIRTY_AREA_RATIO * viewport_area:
            self._clear_flags()
            return self._full_repaint(
                f"dirty area >= {FALLBACK_DIRTY_AREA_RATIO:.0%} of viewport")
        if len(rects) >= FALLBACK_DIRTY_RECT_COUNT:
            self._clear_flags()
            return self._full_repaint(
                f"{len(rects)} dirty rects >= {FALLBACK_DIRTY_RECT_COUNT}")

        self.surface.reset_stats()
        # The flags decided *where* to repaint; within each dirty rect the
        # full paint order must still be replayed, because erasing the rect
        # also wiped pixels of untouched lower objects that show through
        # translucent / non-covering geometry.  Replaying only the flagged
        # entries here would leave holes (verified by the experiment's
        # pixel comparison).
        replay = 0
        for dr in rects:
            self.surface.fill_bg_rect(dr)
            self.surface.push_clip(dr)
            for entry in entries:
                if not entry.bounds.intersects(dr):
                    continue
                for cmd in entry.commands:
                    execute_command(self.surface, cmd)
                replay += 1
            self.surface.pop_clip()
        result = self._result(rects, False, "", replay, len(entries))
        self._clear_flags()
        return result

    def _clear_flags(self) -> None:
        self.flagged.clear()
        self.old_bounds.clear()


class NaiveNodeDirtyUpdater(NodeDirtyUpdater):
    """Negative control: pure per-object flags, nothing else.

    Only flagged entries are repainted, and only inside their own bounds.
    There is no dependency expansion and no full-order replay, so this is
    the literal "repaint exactly the nodes whose dirty bit is set" design.
    It stays pixel-correct only while flagged bounds never cover pixels
    owned by another living object (disjoint opaque geometry).  Two effects
    break it elsewhere, and the experiment measures both:

    * a moved/deleted object leaves residue because old bounds are erased
      but no surviving object is repainted there;
    * an untouched translucent neighbour above a flagged object is never
      recomposed, so source-over pixels under it keep stale colours.
    """

    name = "naive_node_dirty"

    def update(self) -> UpdateResult:
        if not self.flagged and not self.old_bounds:
            return self._result([], False, "", 0, 0)
        entries = self._build_entries()
        self.last_entries = entries
        by_key = {(e.kind, e.obj_id): i for i, e in enumerate(entries)}
        flagged_idx = sorted(by_key[k] for k in self.flagged if k in by_key)
        residue = merge_rects(self.old_bounds, self._viewport())
        rects = merge_rects(
            list(residue) + [entries[j].bounds for j in flagged_idx],
            self._viewport())
        self.surface.reset_stats()
        replay = 0
        for dr in rects:
            # Object-level redraw still has to clear its own box first,
            # otherwise the previous label's glyph pixels survive under the
            # new ones.  Nothing outside flagged bounds is touched.
            self.surface.fill_bg_rect(dr)
            self.surface.push_clip(dr)
            # Strict flag semantics: replay flagged entries only, no
            # neighbour pull-in and no dependency expansion.
            for j in flagged_idx:
                entry = entries[j]
                if not entry.bounds.intersects(dr):
                    continue
                for cmd in entry.commands:
                    execute_command(self.surface, cmd)
                replay += 1
            self.surface.pop_clip()
        result = self._result(rects, False, "", replay, len(entries))
        self._clear_flags()
        return result


class RegionPropagationUpdater(_BaseUpdater):
    """Paint-region propagation strategy (the production kernel's model).

    Edited objects contribute old *and* new device bounds; bounds are merged
    only where they actually touch.  Inside every resulting dirty rect the
    complete display list is replayed in global paint order, so overlapped
    translucent objects are recomposed for free.
    """

    name = "region_propagation"

    def __init__(self, scene, width: int, height: int, **kw):
        super().__init__(scene, width, height, **kw)
        self._pending: list[Rect] = []

    def invalidate(self, keys, old_bounds=None) -> None:
        """Mirror RenderKernel.invalidate_objects: old + new bounds."""
        if old_bounds:
            self._pending.extend(old_bounds)
        wanted = set(keys)
        for entry in self._build_entries():
            if (entry.kind, entry.obj_id) in wanted:
                self._pending.append(entry.bounds)

    def update(self) -> UpdateResult:
        if not self._pending:
            return self._result([], False, "", 0, 0)
        entries = self._build_entries()
        self.last_entries = entries
        rects = merge_rects(self._pending, self._viewport())
        self._pending.clear()

        viewport_area = self.w * self.h
        if sum(r.area for r in rects) >= FALLBACK_DIRTY_AREA_RATIO * viewport_area:
            return self._full_repaint(
                f"dirty area >= {FALLBACK_DIRTY_AREA_RATIO:.0%} of viewport")
        if len(rects) >= FALLBACK_DIRTY_RECT_COUNT:
            return self._full_repaint(
                f"{len(rects)} dirty rects >= {FALLBACK_DIRTY_RECT_COUNT}")

        self.surface.reset_stats()
        replay = 0
        for dr in rects:
            self.surface.fill_bg_rect(dr)
            self.surface.push_clip(dr)
            for entry in entries:
                if not entry.bounds.intersects(dr):
                    continue
                for cmd in entry.commands:
                    execute_command(self.surface, cmd)
                replay += 1
            self.surface.pop_clip()
        return self._result(rects, False, "", replay, len(entries))

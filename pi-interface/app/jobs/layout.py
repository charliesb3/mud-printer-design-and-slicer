"""
Effective lift boundaries over a job's ordered G-code (2026-10-14).

A job's lines are one ORDERED sequence (the authoritative PrintPlan order,
exported lift by lift). A lift boundary is only WHERE in that sequence the
controller stops for a frame reposition. By default the boundaries are the
exported lifts. The operator may move the boundary at the end of the current
lift ("continue into more layers" / "end this lift early") when the physical
layer thickness differs from the nominal one — but ONLY to a CLEAN CUT:

    a layer-mark position where every pass before it has a lower layer than
    every pass after it (so no layer is split, and the global order —
    including the planner's island order — is unchanged), and which starts a
    RE-ENTRY segment (`reentry_marks`: units + distance mode + an explicit
    travel with feed to the pass start), so the controller can stop and
    resume there without generating any motion of its own.

Moving a boundary never reorders, adds, removes or alters a line: the same
lines are sent in the same order; only the pause points change. In buildings
whose islands interleave inside a lift there are no clean cuts there (only at
the exported boundaries and above merges) — nothing is offered there.
Physical heights are the operator's (observed layer thickness × layers); the
Pi refuses a lift estimated taller than the machine's Z travel.
"""
from __future__ import annotations

import bisect
import statistics
from typing import Optional


class LiftLayout:
    def __init__(self, lifts, lift_plan: Optional[dict] = None, reentry: bool = False) -> None:
        self.lifts = lifts
        self.lift_plan = lift_plan or {}
        self.reentry = reentry
        self.offsets: list[int] = []
        n = 0
        for lift in lifts:
            self.offsets.append(n)
            n += len(lift.lines)
        self.n = n
        self.boundaries: list[int] = list(self.offsets)          # effective lift starts (incl. 0)
        self._bset = set(self.boundaries)
        # segments: one per layer mark, global order: (g, layer, z)
        self.segments: list[tuple] = []
        for li, lift in enumerate(lifts):
            for m in lift.marks:
                if isinstance(m.get("layer"), int):
                    self.segments.append((self.offsets[li] + m["line"], m["layer"], m.get("z")))
        self.seg_g = [s[0] for s in self.segments]
        self.layer_top: dict[int, float] = {}
        for _, layer, z in self.segments:
            if isinstance(z, (int, float)):
                self.layer_top[layer] = max(self.layer_top.get(layer, z), z)
        self.layer_bottom: dict[int, float] = {}
        for li, lift in enumerate(lifts):
            first = (lift.meta.get("layers") or {}).get("first")
            if isinstance(first, int) and isinstance(lift.meta.get("z_bottom"), (int, float)):
                self.layer_bottom[first] = lift.meta["z_bottom"]
        for layer in sorted(self.layer_top):
            if layer not in self.layer_bottom and (layer - 1) in self.layer_top:
                self.layer_bottom[layer] = self.layer_top[layer - 1]
        self.nominal_layer = self._nominal_layer()
        self.cuts = self._clean_cuts()
        # adaptive only when every lift carries layer marks + re-entry segments
        self.adjustable = bool(reentry and self.segments and all(l.marks for l in lifts) and len(self.segments) > 1)

    # ---------------------------------------------------------------- #

    def _nominal_layer(self) -> Optional[float]:
        lh = self.lift_plan.get("layer_height")
        if isinstance(lh, (int, float)) and lh > 0:
            return float(lh)
        th = [self.layer_top[l] - self.layer_bottom[l] for l in self.layer_top if l in self.layer_bottom]
        th = [t for t in th if t > 0]
        return statistics.median(th) if th else None

    def _clean_cuts(self) -> set:
        """Segment starts where all earlier passes are below all later ones."""
        layers = [s[1] for s in self.segments]
        if not layers:
            return set()
        suf = [0] * len(layers)
        m = 10 ** 9
        for i in range(len(layers) - 1, -1, -1):
            m = min(m, layers[i])
            suf[i] = m
        cuts, pre = set(), -10 ** 9
        for i in range(len(layers)):
            if i > 0 and pre < suf[i]:
                cuts.add(self.seg_g[i])
            pre = max(pre, layers[i])
        return cuts

    # ---------------------------------------------------------------- #

    @property
    def count(self) -> int:
        return len(self.boundaries)

    def is_boundary(self, g: int) -> bool:
        return g in self._bset

    def lift_of(self, g: int) -> int:
        return max(0, bisect.bisect_right(self.boundaries, g) - 1)

    def range_of(self, k: int) -> tuple:
        start = self.boundaries[k]
        end = self.boundaries[k + 1] if k + 1 < len(self.boundaries) else self.n
        return start, end

    def segment_at(self, g: int) -> Optional[tuple]:
        """The segment (pass) in effect once `g` lines have been sent."""
        if not self.segments:
            return None
        i = bisect.bisect_right(self.seg_g, g) - 1
        return self.segments[max(0, i)]

    def layers_in(self, a: int, b: int) -> list:
        i, j = bisect.bisect_left(self.seg_g, a), bisect.bisect_left(self.seg_g, b)
        return sorted({s[1] for s in self.segments[i:j]})

    def completed_layers(self, a: int, b: int, done: int) -> int:
        """Layers of lines [a, b) whose every pass has been fully sent by line `done`."""
        i, j = bisect.bisect_left(self.seg_g, a), bisect.bisect_left(self.seg_g, b)
        last_end: dict = {}
        for idx in range(i, j):
            end = self.seg_g[idx + 1] if idx + 1 < len(self.seg_g) else self.n
            end = min(end, b)
            last_end[self.segments[idx][1]] = max(last_end.get(self.segments[idx][1], 0), end)
        return sum(1 for e in last_end.values() if e <= done)

    def original_index(self, start: int, end: int) -> Optional[int]:
        """The exported lift exactly matching [start, end), if unchanged."""
        if start in self.offsets:
            li = self.offsets.index(start)
            o_end = self.offsets[li + 1] if li + 1 < len(self.offsets) else self.n
            if o_end == end:
                return li
        return None

    def frame_base(self, k: int) -> Optional[float]:
        start, end = self.range_of(k)
        if start in self.offsets:
            zb = self.lifts[self.offsets.index(start)].meta.get("z_bottom")
            if isinstance(zb, (int, float)):
                return float(zb)
        layers = self.layers_in(start, end)
        if layers and layers[0] in self.layer_bottom:
            return self.layer_bottom[layers[0]]
        return None

    def describe(self, k: int) -> dict:
        """The effective lift k for display (exported metadata when unchanged)."""
        start, end = self.range_of(k)
        oi = self.original_index(start, end)
        if oi is not None:
            d = self.lifts[oi].to_dict()
            d["index"] = k
            d["adjusted"] = False
        else:
            d = {"index": k, "total_lines": end - start, "adjusted": True}
            if start in self.offsets:
                orig = self.lifts[self.offsets.index(start)]
                if orig.reposition_before:
                    d["reposition_before"] = orig.reposition_before
        layers = self.layers_in(start, end)
        if layers:
            d["layers"] = {"first": layers[0], "last": layers[-1], "count": len(layers)}
            d["description"] = f"Layers {layers[0] + 1}–{layers[-1] + 1}" + ("" if oi is not None else " (adjusted)")
            base = self.frame_base(k)
            if base is not None:
                d["z_bottom"] = round(base, 6)
            if layers[-1] in self.layer_top:
                d["z_top"] = round(self.layer_top[layers[-1]], 6)
        if d.get("adjusted") and k > 0 and "reposition_before" not in d:
            prev = self.frame_base(k - 1)
            base = self.frame_base(k)
            d["reposition_before"] = {"adjusted": True,
                                      **({"rise": round(base - prev, 6)} if base is not None and prev is not None else {})}
        d["start_line"], d["end_line"] = start, end
        return d

    # ---------------------------------------------------------------- #
    # adjusting the end of a lift

    def candidates(self, k: int, after: int, t_obs: Optional[float], max_z: Optional[float],
                   target: Optional[float], limit: int = 120) -> list:
        """Possible new ends for effective lift k: clean cuts after line
        `after` (lines already sent stay put), plus the end of the job. The
        list is not cut at the Z-travel limit (flags only): a client may
        re-evaluate it for another layer thickness."""
        start, cur_end = self.range_of(k)
        out = []
        for c in sorted(c for c in self.cuts if c > max(start, after)) + [self.n]:
            layers = self.layers_in(start, c)
            if not layers:
                continue
            phys = len(layers) * t_obs if t_obs else None
            out.append({
                "at": c,
                "end_layer": layers[-1],
                "layers": len(layers),
                "nominal_height": round(self.layer_top.get(layers[-1], 0.0) - (self.frame_base(k) or 0.0), 6)
                if layers[-1] in self.layer_top else None,
                "physical_estimate": round(phys, 4) if phys is not None else None,
                "exceeds_max": bool(max_z is not None and phys is not None and phys > max_z + 1e-9),
                "exceeds_target": bool(target is not None and phys is not None and phys > target + 1e-9),
                "planned": c == cur_end,
                "end_of_job": c == self.n,
            })
            if len(out) >= limit:
                break
        return out

    def set_end(self, k: int, c: int) -> None:
        """End effective lift k before line c (c = n: no further boundary)."""
        start, nb = self.range_of(k)
        keep_from = max(c, nb)
        new = [b for b in self.boundaries if b <= start or b > keep_from]
        if c < self.n:
            new.append(c)
        self.boundaries = sorted(set(new))
        self._bset = set(self.boundaries)

"""
Motion analysis of a job's G-code (2026-10-14) — the controller's own reading
of the machine instructions it will send. Nothing here knows the Designer:
estimates, distances and the 2D path view all come from the G-code lines
(plus the job's optional `travel_lines` classification).

ESTIMATE MODEL (deliberately simple, always labelled an estimate):
- a feed move (G1/G2/G3) takes XY length / F (F modal, in the active units
  per minute); arcs are approximated by their chord;
- a rapid (G0) takes length / the GRBL max rate when known ($110/$111),
  otherwise its time is unknown and counted in `unknown_lines`;
- G4 P<s> dwells add their time;
- NOT modelled: acceleration, cornering, feed override, holds, operator
  waits at frame repositions. Times are at 100 % feed override.
Distances are reported in inches (G21 lengths converted).
"""
from __future__ import annotations

import re
from array import array
from dataclasses import dataclass, field
from typing import Optional

MM_PER_IN = 25.4
_WORD_RE = re.compile(r'([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))')


@dataclass
class MotionProfile:
    """Cumulative values over the job's sendable lines: index i = before line i
    (length n + 1). Points: one per XY move (line index, x, y in inches,
    kind 0 = print / feed, 1 = travel)."""
    time_s: array = field(default_factory=lambda: array('d', [0.0]))
    print_in: array = field(default_factory=lambda: array('d', [0.0]))
    path_in: array = field(default_factory=lambda: array('d', [0.0]))
    pt_line: array = field(default_factory=lambda: array('l'))
    pt_x: array = field(default_factory=lambda: array('d'))
    pt_y: array = field(default_factory=lambda: array('d'))
    pt_kind: array = field(default_factory=lambda: array('b'))
    unknown_lines: int = 0               # moves whose time could not be estimated
    classified: bool = False             # travel vs print known from the job (travel_lines)

    def between(self, a: int, b: int) -> dict:
        """Totals for lines [a, b)."""
        return {
            "time_s": self.time_s[b] - self.time_s[a],
            "print_in": self.print_in[b] - self.print_in[a],
            "path_in": self.path_in[b] - self.path_in[a],
        }

    def bounds(self, a: int = 0, b: Optional[int] = None) -> Optional[dict]:
        """XY extent (inches, work coordinates) of the moves of lines [a, b)."""
        b = len(self.time_s) - 1 if b is None else b
        xs = [self.pt_x[i] for i in range(len(self.pt_line)) if a <= self.pt_line[i] < b]
        if not xs:
            return None
        ys = [self.pt_y[i] for i in range(len(self.pt_line)) if a <= self.pt_line[i] < b]
        return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def analyze(lines: list[str], travel: Optional[set] = None,
            rapid_in_min: Optional[float] = None) -> MotionProfile:
    """Walk the (comment-stripped) lines with GRBL's modal state."""
    p = MotionProfile(classified=travel is not None)
    t = pr = pa = 0.0
    inch, absolute, motion, feed = False, True, 0, 0.0   # GRBL power-up: G21 G90 G0
    x = y = 0.0
    for i, raw in enumerate(lines):
        u = raw.upper().replace(' ', '')
        if u.startswith('$') or u.startswith('%'):
            p.time_s.append(t); p.print_in.append(pr); p.path_in.append(pa)
            continue
        words = _WORD_RE.findall(u)
        gs = [float(v) for k, v in words if k == 'G']
        vals = {k: float(v) for k, v in words if k != 'G'}
        for g in gs:
            if g == 20:
                inch = True
            elif g == 21:
                inch = False
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute = False
            elif g in (0, 1, 2, 3):
                motion = int(g)
        if 'F' in vals:
            feed = vals['F']
        if 4 in gs and 'P' in vals:
            t += vals['P']
        elif ('X' in vals or 'Y' in vals) and 53 not in gs:
            nx = vals.get('X', x if absolute else 0.0)
            ny = vals.get('Y', y if absolute else 0.0)
            if not absolute:
                nx, ny = x + nx, y + ny
            length = ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5           # active units
            f_in = 1.0 if inch else 1.0 / MM_PER_IN
            length_in = length * f_in
            is_travel = (i in travel) if travel is not None else motion == 0
            if motion == 0:
                if rapid_in_min:
                    t += length_in / rapid_in_min * 60.0
                elif length > 0:
                    p.unknown_lines += 1
            elif feed > 0:
                t += length / feed * 60.0
            elif length > 0:
                p.unknown_lines += 1
            pa += length_in
            if not is_travel:
                pr += length_in
            x, y = nx, ny
            p.pt_line.append(i)
            p.pt_x.append(x * f_in)
            p.pt_y.append(y * f_in)
            p.pt_kind.append(1 if is_travel else 0)
        p.time_s.append(t)
        p.print_in.append(pr)
        p.path_in.append(pa)
    return p


# 2D VIEW SIMPLIFICATION (2026-10-15). The earlier version kept every k-th
# point (index stride) and drew straight chords across the dropped ones: on a
# 375k-move job that cut right-angle corners by up to 9 in — shortcuts that
# are NOT in the machine path. Now: Ramer–Douglas–Peucker per run, so EVERY
# original vertex lies within `tolerance_in` of the drawn path; kept points
# are exact G-code coordinates; runs break at print / travel changes and at
# every `breaks` line (pass / lift starts), so those points are always kept.
VIEW_TOLERANCE_IN = 0.1          # sub-pixel at the fitted view of a building
VIEW_MAX_TOLERANCE_IN = 0.25     # never coarser than this, whatever the size
VIEW_POINT_BUDGET = 150_000      # above it the tolerance is relaxed (≤ max)


def _rdp_keep(xs, ys, a: int, b: int, eps: float, keep: bytearray) -> None:
    """Mark the vertices of polyline [a, b) needed so that every vertex lies
    within `eps` of the kept polyline (iterative Ramer–Douglas–Peucker)."""
    keep[a] = keep[b - 1] = 1
    stack = [(a, b - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = xs[i], ys[i]
        dx, dy = xs[j] - ax, ys[j] - ay
        l2 = dx * dx + dy * dy
        best, bk = -1.0, -1
        for k in range(i + 1, j):
            px, py = xs[k] - ax, ys[k] - ay
            if l2 == 0.0:
                d = px * px + py * py
            else:
                t = (px * dx + py * dy) / l2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                ex, ey = px - t * dx, py - t * dy
                d = ex * ex + ey * ey
            if d > best:
                best, bk = d, k
        if best > eps * eps:
            keep[bk] = 1
            stack.append((i, bk))
            stack.append((bk, j))


def simplify_keep(p: MotionProfile, eps: float, breaks: Optional[set] = None) -> bytearray:
    """Keep-mask over the profile points for tolerance `eps` (inches)."""
    n = len(p.pt_line)
    keep = bytearray(n)
    breaks = breaks or set()
    s = 0
    for i in range(1, n + 1):
        if i == n or p.pt_kind[i] != p.pt_kind[i - 1] or p.pt_line[i] in breaks:
            _rdp_keep(p.pt_x, p.pt_y, s, i, eps, keep)
            s = i
    return keep


def path_payload(p: MotionProfile, breaks: Optional[set] = None,
                 tolerance_in: float = VIEW_TOLERANCE_IN, budget: int = VIEW_POINT_BUDGET) -> dict:
    """The 2D path for the live view: every XY move of the G-code, simplified
    with a BOUNDED error (see above). The segment ending at point i is the
    move of line `line[i]` with kind `kind[i]`."""
    n = len(p.pt_line)
    eps = tolerance_in
    keep = simplify_keep(p, eps, breaks)
    while sum(keep) > budget and eps < VIEW_MAX_TOLERANCE_IN:
        eps = min(VIEW_MAX_TOLERANCE_IN, eps * 1.6)
        keep = simplify_keep(p, eps, breaks)
    idx = [i for i in range(n) if keep[i]]
    return {
        "line": [p.pt_line[i] for i in idx],
        "x": [round(p.pt_x[i], 4) for i in idx],
        "y": [round(p.pt_y[i], 4) for i in idx],
        "kind": [p.pt_kind[i] for i in idx],
        "decimated": len(idx) < n,
        "points": n,
        "tolerance_in": eps,
    }

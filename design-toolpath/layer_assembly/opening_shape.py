"""
SELF-SUPPORTING OPENINGS — ONE configurable opening and its DIMENSIONAL
CONSTRAINTS (revised 2026-10-10, third pass: the Diamond / Arch distinction
removed — both diamonds are configurations of the one opening).

Pure math, mirrored line for line by design_proto/static/opening_shape.js:
the UI CONSTRAINS an opening while it is edited (the stored parameters are
always the printable ones), profiles.py resolves and VALIDATES exactly the
same numbers. tests/test_opening_shape_parity.py runs both implementations.

THE OPENING — three independent components, bottom to top:
  LOWER  `lower`, depth `bottom_depth`:
           'flat'    sill / a door at Z 0 (no depth)
           'pointed' Angular: straight sides to a sharp bottom point
           'arc'     Curved: two circular arcs centred on its top line —
                     vertical where they meet the sides, a SHARP bottom
                     point (the lower half of a vesica); only pointed when
                     deeper than W / 2
           'round'   Rounded: half ellipse, horizontal at the bottom
                     (depth W / 2 = a true semicircle)
  MIDDLE optional rectangle: `middle` on / off, `middle_height` (kept while off)
  UPPER  `upper`, height `upper_height`:
           'pointed' Angular: two straight sides to a sharp apex
           'curve'   Curved: Gothic — two circular arcs centred on the spring
                     line, vertical at the middle, sharp apex (U > W / 2)
           'header'  flat top carried by a physical HEADER (profiles.py
                     generates it): no closure, no overhang constraint
  Total height H = lower depth + middle + upper height (derived; stored in
  `height` for arrays / Z ranges).
  Angular upper + Angular lower, no middle = the angular DIAMOND; Curved
  upper + Curved lower of equal heights, no middle = the curved diamond
  (vesica). Older Diamond / Lens / Arch openings are MIGRATED (`migrate`,
  `fill`).

LAYERS: one global layer height lh (model.py), layer k = [k·lh, (k+1)·lh]; a
layer's cut is the shape's width at its MID-HEIGHT; a layer the shape crosses
more narrowly than w_min is cut w_min wide (never dropped).

Z DIMENSIONS: the UI keeps what the user types / drags on WHOLE LAYERS (the
Assembly convention — with the bottom on a layer every boundary of the shape
is one, so a middle / lower / bottom edit never changes what the upper
needs), and the searches below step whole layers; the solver itself never
re-snaps stored values (a migrated diamond of an odd number of layers keeps
its half-layer spring, i.e. its geometry). Widths: 1/16 in.

PRINTABLE ⇔ the UPPER narrows each side by ≤ h (Maximum overhang, ≤ the
bead) from layer to layer, the final closure to a full layer included — on
the ACTUAL layers of every opening (an array's distinct layer phases each).
The lower profile only widens upward: not a closure.

PRECEDENCE (`constrain`): the dimension edited LAST wins.
  upper height → keep it; W shrinks to the widest printable width (1/16 in)
  width        → keep it; the upper height rises to the first printable
                 value (whole layers); a Curved lower too shallow to stay
                 pointed is deepened
  anything else (profiles, middle, lower depth, overhang, layer height, bead
  width, array, load) → the profile's DRIVER (its last edited of the two;
  default width) decides
Nothing else changes (position, bottom Z, middle, arrays). A conflict no
dimension can resolve is REPORTED, never hidden.
"""
from __future__ import annotations

import math

EPS = 1e-9
Q = 16.0                       # widths: 1/16 in
MIN_OPENING_BEADS = 0.25       # the smallest cut (a sliver is cut this wide; ≤ 2 h)
MAX_DIM = 4000.0               # search bound (in)
UPPERS = ('pointed', 'curve', 'header')
LOWERS = ('flat', 'pointed', 'arc', 'round')
HEADER_DEFAULTS = {'bearing': 8.0, 'depth': None, 'thickness': 6.0}   # depth None: wall thickness + 1 bead


def wmin(bead, h):
    return min(MIN_OPENING_BEADS * bead, 2.0 * h) if h > 0 else MIN_OPENING_BEADS * bead


def q16(x):
    return round(float(x) * Q) / Q


def _clean(x):
    return round(x, 6)


def zsnap(x, lh, least=0.0):
    """A Z dimension → whole layers (≥ least)."""
    return _clean(max(least, round(float(x) / lh) * lh))


def snap_z(s, lh):
    """The opening's Z dimensions → whole layers (in place) — a layer-height
    change (the UI), never the solver."""
    if s.get('upper_height') is not None and s['upper'] != 'header':
        s['upper_height'] = zsnap(s['upper_height'], lh, lh)
    if s.get('middle_height') is not None:
        s['middle_height'] = zsnap(s['middle_height'], lh)
    if s.get('bottom_depth') is not None and s.get('lower') != 'flat':
        s['bottom_depth'] = zsnap(s['bottom_depth'], lh, lh)
    sync(s)


def upper_half(upper, W, U, u):
    """Half width of an UPPER profile of width W and height U at u above its
    spring. 'curve': the pointed (Gothic) arc — a circle centred on the
    spring line, a = (U² − W²/4) / W from the axis on the far side; it is
    pointed only when U > W / 2 (else a round fallback, never printable)."""
    if u >= U or U <= 0.0:
        return 0.0
    if upper == 'curve':
        a = (U * U - 0.25 * W * W) / W if W > EPS else 0.0
        if a <= EPS:
            return 0.5 * W * math.sqrt(max(0.0, 1.0 - (u / U) * (u / U)))
        R = a + 0.5 * W
        return max(0.0, math.sqrt(max(0.0, R * R - u * u)) - a)
    return 0.5 * W * (1.0 - u / U)


def lower_half(lower, W, d, t):
    """Half width of the LOWER profile (depth d) at t above its bottom."""
    if lower == 'flat' or d <= 0.0 or t >= d:
        return 0.5 * W
    if lower == 'arc':                        # the vesica's lower half: sharp bottom point
        return upper_half('curve', W, d, d - t)
    if lower == 'round':                      # half ellipse: x² + (1 − t/d)² = 1
        v = 1.0 - t / d
        return 0.5 * W * math.sqrt(max(0.0, 1.0 - v * v))
    return 0.5 * W * t / d


def migrate(s):
    """A shape dict of an older project → the one opening (in place; True =
    changed). Context-free; an older closure-based ARCH also needs `fill`.
      Diamond (H, style)    → upper = lower = its style (Curved: upper 'curve'
                              + lower 'arc'), no middle, upper height = lower
                              depth = H / 2 — the identical shape
      Lens                  → the curved diamond (the closest; the Lens's own
                              cos / sine flanks no longer exist — since the
                              2026-10-10 first pass)
      Arch                  → its components; lower 'curve' (the half ellipse
                              until the sharp Curved lower) / semicircle /
                              ellipse → 'round'; `closure` → upper height
      `shape` is dropped."""
    ch = False
    shape = s.get('shape')
    if 'shape' in s:
        del s['shape']
        ch = True
    if not s.get('upper'):
        s['upper'] = 'pointed'
        ch = True
    W = float(s.get('width') or 0.0)
    if shape in ('diamond', 'lens') or (shape is None and s.get('middle') is None and s.get('upper_height') is None
                                        and s.get('upper') != 'header'):
        H = float(s.get('height') or 0.0)
        style = 'curve' if shape == 'lens' or s['upper'] == 'curve' else 'pointed'
        s['upper'], s['lower'] = style, 'arc' if style == 'curve' else 'pointed'
        s['bottom_depth'] = s['upper_height'] = _clean(0.5 * H)
        s['middle'], s['middle_height'] = False, None
        if 'closure' in s:
            del s['closure']
        if s.get('driver') == 'height':
            s['driver'] = 'upper_height'
        return True
    if s.get('lower') == 'curve':
        s['lower'], ch = 'round', True
    if s.get('lower') == 'semicircle':
        s['lower'], s['bottom_depth'], ch = 'round', None, True
    elif s.get('lower') == 'ellipse':
        bd = s.get('bottom_depth')
        s['lower'] = 'round'
        s['bottom_depth'] = float(bd) if bd is not None else q16(0.25 * W)
        ch = True
    if not s.get('lower'):
        s['lower'] = 'flat'
        ch = True
    if s['lower'] == 'flat':
        if s.get('bottom_depth') is not None:
            s['bottom_depth'] = None
            ch = True
    elif s.get('bottom_depth') is None and s['lower'] in ('pointed', 'round', 'arc'):
        s['bottom_depth'] = q16(0.5 * W)          # (the earlier automatic depth: a semicircle / 45° point)
        ch = True
    if s.get('closure') is not None:              # an older explicit closure = the upper height
        if s.get('upper_height') is None:
            s['upper_height'] = float(s['closure'])
        ch = True
    if 'closure' in s:
        del s['closure']
    if s.get('driver') == 'height':
        s['driver'], ch = 'upper_height', True
    return ch


def parts(s):
    """(d, m, U, H): lower depth, middle height, upper height, total."""
    d = 0.0 if s['lower'] == 'flat' else max(0.0, float(s.get('bottom_depth') or 0.0))
    m = max(0.0, float(s.get('middle_height') or 0.0)) if s.get('middle') else 0.0
    U = 0.0 if s['upper'] == 'header' else max(0.0, float(s.get('upper_height') or 0.0))
    return d, m, U, d + m + U


def sync(s):
    """The stored total height = the components."""
    s['height'] = _clean(parts(s)[3])


def width_at(t, W, d, m, U, upper, lower):
    """The opening's width at t inches above its bottom (continuous)."""
    H = d + m + U
    if t < 0.0 or t >= H:
        return 0.0
    sp = d + m
    if t >= sp:
        return 2.0 * upper_half(upper, W, U, t - sp)
    if t < d:
        return 2.0 * lower_half(lower, W, d, t)
    return W


def layer_widths(zb, W, d, m, U, upper, lower, lh, wm):
    """[(layer k, cut width)] of the layers whose mid-height lies in the opening."""
    H = d + m + U
    out = []
    k = max(0, int(math.floor(zb / lh)) - 1)
    while True:
        z = (k + 0.5) * lh
        if z >= zb + H:
            break
        if z >= zb:
            w = width_at(z - zb, W, d, m, U, upper, lower)
            out.append((k, 0.0 if w <= EPS else max(w, wm)))
        k += 1
    return out


def upper_worst(W, upper, U, spring, lh, wm):
    """The largest side step of an UPPER closure (full width W below
    `spring`, closed at spring + U) on the layers, the closure to a full
    layer above included."""
    worst, prev = 0.0, W
    k = max(0, int(math.floor(spring / lh)) - 1)
    while True:
        z = (k + 0.5) * lh
        if z >= spring + U:
            break
        if z >= spring:
            w = 2.0 * upper_half(upper, W, U, z - spring)
            w = 0.0 if w <= EPS else max(w, wm)
            if w < prev:
                worst = max(worst, 0.5 * (prev - w))
            prev = w
        k += 1
    return max(worst, 0.5 * prev)


def array_offsets(a, W, H):
    """[(Δposition, Δz_bottom)] of an array's openings from the lower-left one
    (clear spacing: pitch = W + h_spacing, H + v_spacing)."""
    if not a:
        return [(0.0, 0.0)]
    ar = a.get('arrangement') or 'single'
    px, pz = W + float(a.get('h_spacing') or 0.0), H + float(a.get('v_spacing') or 0.0)
    n = max(1, int(a.get('count') or 1))
    if ar == 'horizontal':
        return [(i * px, 0.0) for i in range(n)]
    if ar == 'vertical':
        return [(0.0, i * pz) for i in range(n)]
    if ar == 'diagonal_up':
        return [(i * px, i * pz) for i in range(n)]
    if ar == 'diagonal_down':
        return [(i * px, (n - 1 - i) * pz) for i in range(n)]
    if ar in ('grid', 'staggered'):
        st = 0.0
        if ar == 'staggered':
            st = 0.5 * px if a.get('stagger') is None else float(a['stagger'])
        return [(c * px + (r % 2) * st, r * pz) for r in range(max(1, int(a.get('rows') or 1)))
                for c in range(max(1, int(a.get('cols') or 1)))]
    return [(0.0, 0.0)]


def _phases(s, lh):
    """One bottom Z per distinct layer phase of the openings."""
    seen, out = set(), []
    for _, dz in array_offsets(s.get('array'), float(s['width']), float(s['height'])):
        zb = float(s['z_bottom']) + dz
        key = round(((zb % lh) + lh) % lh / lh * 1e6) % 1000000
        if key not in seen:
            seen.add(key)
            out.append(zb)
    return out


def springs(s, lh, sp):
    """The spring Z of every distinct layer phase of the openings."""
    return [zb + sp for zb in _phases(s, lh)]


def evaluate(s, lh, h, bead):
    """The resolved shape of a parameter set: {ok, reason, d, m, U, H,
    worst}. reason: 'empty' (no width / height) | 'depth' (a Curved lower not
    deeper than W / 2) | 'overhang' (the closure steps more than h somewhere,
    or a curved closure too flat to be pointed)."""
    W = float(s['width'])
    d, m, U, H = parts(s)
    r = {'ok': False, 'reason': None, 'd': d, 'm': m, 'U': U, 'H': H, 'worst': 0.0}
    if W <= EPS or H <= EPS:
        r['reason'] = 'empty'
        return r
    if s['lower'] == 'arc' and d <= 0.5 * W + EPS:
        r['reason'] = 'depth'
        return r
    if s['upper'] == 'header':
        r['ok'] = True
        return r
    if h <= 0 or U <= EPS or (s['upper'] == 'curve' and U <= 0.5 * W + EPS):
        r['reason'] = 'overhang'
        r['worst'] = 0.5 * W
        return r
    wm = wmin(bead, h)
    r['worst'] = max(upper_worst(W, s['upper'], U, z, lh, wm) for z in springs(s, lh, d + m))
    r['ok'] = r['worst'] <= h + EPS
    if not r['ok']:
        r['reason'] = 'overhang'
    return r


def _with(s, field, v):
    t = dict(s)
    t[field] = v
    sync(t)
    return t


def max_width(s, lh, h, bead, upto):
    """The largest printable width ≤ upto (1/16 in) at s's heights; 0 if none."""
    n_hi = int(math.floor(upto * Q + 1e-6))
    if n_hi <= 0:
        return 0.0
    if evaluate(_with(s, 'width', n_hi / Q), lh, h, bead)['ok']:
        return n_hi / Q
    lo, hi = 0, n_hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if evaluate(_with(s, 'width', mid / Q), lh, h, bead)['ok']:
            lo = mid
        else:
            hi = mid
    return lo / Q


def min_height(s, lh, h, bead, start):
    """The smallest printable UPPER HEIGHT ≥ start (whole layers) at s's
    width; None if none."""
    n0 = max(1, int(math.ceil(start / lh - 1e-6)))

    def ok(n):
        return evaluate(_with(s, 'upper_height', _clean(n * lh)), lh, h, bead)['ok']
    if ok(n0):
        return _clean(n0 * lh)
    lo, step = n0, 1
    while True:
        hi = n0 + step
        if hi * lh > MAX_DIM:
            return None
        if ok(hi):
            break
        lo, step = hi, step * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return _clean(hi * lh)


def fill(s, lh, h, bead):
    """Complete an older closure-based ARCH (from `migrate`: no `middle`):
    its upper height = its old explicit closure, else the minimum printable
    one (whole layers); the rest of its old total height becomes the middle
    (exactly — its total is kept). In place; True = changed."""
    if s.get('middle') is not None:
        return False
    if s['upper'] == 'header':
        s['middle'] = (s.get('middle_height') or 0) > 0
        return True
    d = parts(dict(s, middle=False, upper_height=0.0))[0]
    base = dict(s, middle=False, middle_height=0.0)
    umin = min_height(dict(base, upper_height=lh), lh, h, bead, lh) if float(s['width']) > EPS else None
    U = s.get('upper_height')
    if U is None or (umin is not None and U < umin - 1e-9):
        U = umin if umin is not None else zsnap(0.5 * float(s['width']), lh, lh)
    m = _clean(max(0.0, float(s.get('height') or 0.0) - d - U))
    s['middle'], s['middle_height'] = m > 1e-6, m
    s['upper_height'] = U
    sync(s)
    return True


def defaults(s):
    """Fields a current opening always has."""
    s.setdefault('middle', None)
    for k in ('middle_height', 'upper_height', 'bottom_depth'):
        s.setdefault(k, None)


def limits(s, lh, h, bead):
    """The widest printable opening at this upper height, the lowest
    printable upper height at this width — the hints of an error."""
    t = dict(s)
    migrate(t)
    defaults(t)
    fill(t, lh, h, bead)
    if t['upper'] == 'header':
        return {'max_width': None, 'min_height': None}
    hv = float(t.get('upper_height') or 0.0)
    cap = max(float(t['width']), 1.0) + 4.0 * hv * max(h, 1e-3) / lh
    return {'max_width': max_width(t, lh, h, bead, cap), 'min_height': min_height(t, lh, h, bead, lh)}


DIMS = ('width', 'upper_height', 'bottom_depth')


def arc_min_depth(W, lh):
    """The shallowest Curved (vesica) lower in whole layers: deeper than W / 2."""
    return _clean((math.floor(0.5 * W / lh + 1e-9) + 1) * lh)


def fit_arc_depth(s, lh):
    """A Curved lower not deeper than W / 2 (it would not be pointed) → the
    first whole layer past W / 2 (in place). A deeper one is kept as it is."""
    if s.get('lower') == 'arc':
        if s.get('bottom_depth') is None or float(s['bottom_depth']) <= 0.5 * float(s['width']) + EPS:
            s['bottom_depth'] = arc_min_depth(float(s['width']), lh)
            sync(s)


def constrain(s, edited, lh, h, bead):
    """Make a parameter set printable, the dimension edited LAST winning (see
    the module docstring). s: {upper, lower, width, height, z_bottom,
    bottom_depth, middle, middle_height, upper_height, array, driver}.
    Returns (new dict, info) — info {'changed': {field: [before, after]},
    'conflict': message | None}."""
    s = dict(s)
    before = {k: s.get(k) for k in DIMS}
    migrate(s)
    defaults(s)
    fill(s, lh, h, bead)
    sync(s)
    if edited in ('width', 'upper_height'):
        s['driver'] = edited
    drv = 'upper_height' if s.get('driver') in ('upper_height', 'height') else 'width'
    # the upper is solved first (a Curved lower's depth rule does not limit it:
    # judged as an angular lower of the same depth), then the lower is deepened
    p = dict(s, lower='pointed') if s['lower'] == 'arc' else s
    conflict = None
    ev = evaluate(p, lh, h, bead)
    if ev['reason'] == 'empty':
        conflict = 'the opening has no height: give it a lower profile, a middle or an upper height' \
            if float(s['width']) > EPS else 'the width must be > 0'
    elif not ev['ok']:
        if drv == 'upper_height':
            w = max_width(p, lh, h, bead, float(s['width']))
            if w > 0:
                s['width'] = w
            else:
                conflict = 'no printable opening has this upper height'
        else:
            v = min_height(p, lh, h, bead, float(s.get('upper_height') or 0.0))
            if v is None:
                conflict = f"no printable upper height for a {float(s['width']):g} in wide opening"
            else:
                s['upper_height'] = v
        sync(s)
    fit_arc_depth(s, lh)
    if conflict is None and not evaluate(s, lh, h, bead)['ok']:
        conflict = 'these dimensions cannot be made printable'
    changed = {k: [before[k], s.get(k)] for k in DIMS
               if before[k] is not None and s.get(k) is not None and abs(float(before[k]) - float(s[k])) > 1e-9}
    return s, {'changed': changed, 'conflict': conflict}


def min_upper_fixed(W, upper, spring, lh, h, bead):
    """A CONVERTED Designer opening's closure: the minimum printable upper
    height (whole layers) above a fixed spring (its rectangle's top) on the
    actual layers; None if none."""
    if W <= EPS:
        return 0.0
    if h <= 0:
        return None
    wm = wmin(bead, h)

    def ok(n):
        U = _clean(n * lh)
        return not (upper == 'curve' and U <= 0.5 * W + EPS) and upper_worst(W, upper, U, spring, lh, wm) <= h + EPS
    lo, hi = 0, 1
    while not ok(hi):
        lo, hi = hi, hi * 2
        if hi * lh > MAX_DIM:
            return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return _clean(hi * lh)

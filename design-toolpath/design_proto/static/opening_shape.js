// ===========================================================================
// SELF-SUPPORTING OPENINGS — ONE configurable opening + its DIMENSIONAL
// CONSTRAINTS (2026-10-10, third pass: no Diamond / Arch distinction) — a
// line-for-line mirror of layer_assembly/opening_shape.py (read its
// docstring: components, layers, printability, precedence, migration). The
// Assembly UI constrains an opening WHILE it is edited, so the stored
// parameters — the fields, the drawn outline, the resolved layers — are one
// shape; the backend validates the same numbers
// (tests/test_opening_shape_parity.py runs both).
// ===========================================================================
(function (root) {
  const EPS = 1e-9, Q = 16, MIN_OPENING_BEADS = 0.25, MAX_DIM = 4000;
  const UPPERS = ['pointed', 'curve', 'header'];
  const LOWERS = ['flat', 'pointed', 'arc', 'round'];
  const HEADER_DEFAULTS = { bearing: 8, depth: null, thickness: 6 };
  const clean = x => Math.round(x * 1e6) / 1e6;
  // Python round(): half to even (the only rounding both sides share)
  const pyRound = x => { const f = Math.floor(x), r = x - f; return r > 0.5 ? f + 1 : r < 0.5 ? f : (f % 2 === 0 ? f : f + 1); };
  const q16 = x => pyRound(+x * Q) / Q;
  const zsnap = (x, lh, least = 0) => clean(Math.max(least, pyRound(+x / lh) * lh));

  // the Z dimensions → whole layers (a layer-height change; never the solver)
  function snapZ(s, lh) {
    if (s.upper_height != null && s.upper !== 'header') s.upper_height = zsnap(s.upper_height, lh, lh);
    if (s.middle_height != null) s.middle_height = zsnap(s.middle_height, lh);
    if (s.bottom_depth != null && s.lower !== 'flat') s.bottom_depth = zsnap(s.bottom_depth, lh, lh);
    sync(s);
  }

  const wmin = (bead, h) => (h > 0 ? Math.min(MIN_OPENING_BEADS * bead, 2 * h) : MIN_OPENING_BEADS * bead);

  function upperHalf(upper, W, U, u) {
    if (u >= U || U <= 0) return 0;
    if (upper === 'curve') {
      const a = W > EPS ? (U * U - 0.25 * W * W) / W : 0;
      if (a <= EPS) return 0.5 * W * Math.sqrt(Math.max(0, 1 - (u / U) * (u / U)));
      const R = a + 0.5 * W;
      return Math.max(0, Math.sqrt(Math.max(0, R * R - u * u)) - a);
    }
    return 0.5 * W * (1 - u / U);
  }

  function lowerHalf(lower, W, d, t) {
    if (lower === 'flat' || d <= 0 || t >= d) return 0.5 * W;
    if (lower === 'arc') return upperHalf('curve', W, d, d - t);       // the vesica's lower half: sharp point
    if (lower === 'round') { const v = 1 - t / d; return 0.5 * W * Math.sqrt(Math.max(0, 1 - v * v)); }
    return 0.5 * W * t / d;
  }

  // an older project's opening → the one opening (in place; true = changed)
  function migrate(s) {
    let ch = false;
    const shape = s.shape;
    if ('shape' in s) { delete s.shape; ch = true; }
    if (!s.upper) { s.upper = 'pointed'; ch = true; }
    const W = +s.width || 0;
    if (shape === 'diamond' || shape === 'lens' ||
        (shape == null && s.middle == null && s.upper_height == null && s.upper !== 'header')) {
      const H = +s.height || 0;
      const style = shape === 'lens' || s.upper === 'curve' ? 'curve' : 'pointed';
      s.upper = style; s.lower = style === 'curve' ? 'arc' : 'pointed';
      s.bottom_depth = s.upper_height = clean(0.5 * H);
      s.middle = false; s.middle_height = null;
      if ('closure' in s) delete s.closure;
      if (s.driver === 'height') s.driver = 'upper_height';
      return true;
    }
    if (s.lower === 'curve') { s.lower = 'round'; ch = true; }     // (the half ellipse was called Curved)
    if (s.lower === 'semicircle') { s.lower = 'round'; s.bottom_depth = null; ch = true; }
    else if (s.lower === 'ellipse') {
      s.lower = 'round';
      s.bottom_depth = s.bottom_depth != null ? +s.bottom_depth : q16(0.25 * W);
      ch = true;
    }
    if (!s.lower) { s.lower = 'flat'; ch = true; }
    if (s.lower === 'flat') { if (s.bottom_depth != null) { s.bottom_depth = null; ch = true; } }
    else if (s.bottom_depth == null && (s.lower === 'pointed' || s.lower === 'round' || s.lower === 'arc')) { s.bottom_depth = q16(0.5 * W); ch = true; }
    if (s.closure != null) {
      if (s.upper_height == null) s.upper_height = +s.closure;
      ch = true;
    }
    if ('closure' in s) delete s.closure;
    if (s.driver === 'height') { s.driver = 'upper_height'; ch = true; }
    return ch;
  }

  function parts(s) {
    const d = s.lower === 'flat' ? 0 : Math.max(0, +(s.bottom_depth || 0));
    const m = s.middle ? Math.max(0, +(s.middle_height || 0)) : 0;
    const U = s.upper === 'header' ? 0 : Math.max(0, +(s.upper_height || 0));
    return [d, m, U, d + m + U];
  }

  function sync(s) { s.height = clean(parts(s)[3]); }

  function widthAt(t, W, d, m, U, upper, lower) {
    const H = d + m + U;
    if (t < 0 || t >= H) return 0;
    const sp = d + m;
    if (t >= sp) return 2 * upperHalf(upper, W, U, t - sp);
    if (t < d) return 2 * lowerHalf(lower, W, d, t);
    return W;
  }

  function layerWidths(zb, W, d, m, U, upper, lower, lh, wm) {
    const H = d + m + U, out = [];
    let k = Math.max(0, Math.floor(zb / lh) - 1);
    for (;;) {
      const z = (k + 0.5) * lh;
      if (z >= zb + H) break;
      if (z >= zb) {
        const w = widthAt(z - zb, W, d, m, U, upper, lower);
        out.push([k, w <= EPS ? 0 : Math.max(w, wm)]);
      }
      k++;
    }
    return out;
  }

  function upperWorst(W, upper, U, spring, lh, wm) {
    let worst = 0, prev = W, k = Math.max(0, Math.floor(spring / lh) - 1);
    for (;;) {
      const z = (k + 0.5) * lh;
      if (z >= spring + U) break;
      if (z >= spring) {
        let w = 2 * upperHalf(upper, W, U, z - spring);
        w = w <= EPS ? 0 : Math.max(w, wm);
        if (w < prev) worst = Math.max(worst, 0.5 * (prev - w));
        prev = w;
      }
      k++;
    }
    return Math.max(worst, 0.5 * prev);
  }

  function arrayOffsets(a, W, H) {
    if (!a) return [[0, 0]];
    const ar = a.arrangement || 'single';
    const px = W + (+a.h_spacing || 0), pz = H + (+a.v_spacing || 0), n = Math.max(1, Math.floor(+a.count || 1));
    const out = [];
    if (ar === 'horizontal') { for (let i = 0; i < n; i++) out.push([i * px, 0]); return out; }
    if (ar === 'vertical') { for (let i = 0; i < n; i++) out.push([0, i * pz]); return out; }
    if (ar === 'diagonal_up') { for (let i = 0; i < n; i++) out.push([i * px, i * pz]); return out; }
    if (ar === 'diagonal_down') { for (let i = 0; i < n; i++) out.push([i * px, (n - 1 - i) * pz]); return out; }
    if (ar === 'grid' || ar === 'staggered') {
      const st = ar === 'staggered' ? (a.stagger == null ? 0.5 * px : +a.stagger) : 0;
      const rows = Math.max(1, Math.floor(+a.rows || 1)), cols = Math.max(1, Math.floor(+a.cols || 1));
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) out.push([c * px + (r % 2) * st, r * pz]);
      return out;
    }
    return [[0, 0]];
  }

  function phases(s, lh) {
    const seen = new Set(), out = [];
    for (const [, dz] of arrayOffsets(s.array, +s.width, +s.height)) {
      const zb = +s.z_bottom + dz;
      const key = pyRound(((zb % lh) + lh) % lh / lh * 1e6) % 1000000;
      if (!seen.has(key)) { seen.add(key); out.push(zb); }
    }
    return out;
  }

  function springs(s, lh, sp) { return phases(s, lh).map(zb => zb + sp); }

  function evaluate(s, lh, h, bead) {
    const W = +s.width, [d, m, U, H] = parts(s);
    const r = { ok: false, reason: null, d, m, U, H, worst: 0 };
    if (W <= EPS || H <= EPS) { r.reason = 'empty'; return r; }
    if (s.lower === 'arc' && d <= 0.5 * W + EPS) { r.reason = 'depth'; return r; }
    if (s.upper === 'header') { r.ok = true; return r; }
    if (h <= 0 || U <= EPS || (s.upper === 'curve' && U <= 0.5 * W + EPS)) { r.reason = 'overhang'; r.worst = 0.5 * W; return r; }
    const wm = wmin(bead, h);
    r.worst = Math.max(...springs(s, lh, d + m).map(z => upperWorst(W, s.upper, U, z, lh, wm)));
    r.ok = r.worst <= h + EPS;
    if (!r.ok) r.reason = 'overhang';
    return r;
  }

  function withField(s, f, v) { const t = { ...s }; t[f] = v; sync(t); return t; }

  function maxWidth(s, lh, h, bead, upto) {
    const nHi = Math.floor(upto * Q + 1e-6);
    if (nHi <= 0) return 0;
    if (evaluate(withField(s, 'width', nHi / Q), lh, h, bead).ok) return nHi / Q;
    let lo = 0, hi = nHi;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (evaluate(withField(s, 'width', mid / Q), lh, h, bead).ok) lo = mid; else hi = mid;
    }
    return lo / Q;
  }

  function minHeight(s, lh, h, bead, start) {
    const n0 = Math.max(1, Math.ceil(start / lh - 1e-6));
    const ok = n => evaluate(withField(s, 'upper_height', clean(n * lh)), lh, h, bead).ok;
    if (ok(n0)) return clean(n0 * lh);
    let lo = n0, step = 1, hi;
    for (;;) {
      hi = n0 + step;
      if (hi * lh > MAX_DIM) return null;
      if (ok(hi)) break;
      lo = hi; step *= 2;
    }
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (ok(mid)) hi = mid; else lo = mid;
    }
    return clean(hi * lh);
  }

  function fill(s, lh, h, bead) {
    if (s.middle != null) return false;
    if (s.upper === 'header') { s.middle = (s.middle_height || 0) > 0; return true; }
    const d = parts({ ...s, middle: false, upper_height: 0 })[0];
    const base = { ...s, middle: false, middle_height: 0 };
    const umin = +s.width > EPS ? minHeight({ ...base, upper_height: lh }, lh, h, bead, lh) : null;
    let U = s.upper_height;
    if (U == null || (umin != null && U < umin - 1e-9)) U = umin != null ? umin : zsnap(0.5 * +s.width, lh, lh);
    const m = clean(Math.max(0, +(s.height || 0) - d - U));
    s.middle = m > 1e-6; s.middle_height = m;
    s.upper_height = U;
    sync(s);
    return true;
  }

  function defaults(s) {
    if (!('middle' in s)) s.middle = null;
    for (const k of ['middle_height', 'upper_height', 'bottom_depth']) if (!(k in s)) s[k] = null;
  }

  function limits(s, lh, h, bead) {
    const t = { ...s };
    migrate(t); defaults(t); fill(t, lh, h, bead);
    if (t.upper === 'header') return { max_width: null, min_height: null };
    const hv = +(t.upper_height || 0);
    const cap = Math.max(+t.width, 1) + 4 * hv * Math.max(h, 1e-3) / lh;
    return { max_width: maxWidth(t, lh, h, bead, cap), min_height: minHeight(t, lh, h, bead, lh) };
  }

  const DIMS = ['width', 'upper_height', 'bottom_depth'];
  const arcMinDepth = (W, lh) => clean((Math.floor(0.5 * W / lh + 1e-9) + 1) * lh);
  function fitArcDepth(s, lh) {
    if (s.lower === 'arc' && (s.bottom_depth == null || +s.bottom_depth <= 0.5 * +s.width + EPS)) {
      s.bottom_depth = arcMinDepth(+s.width, lh); sync(s);
    }
  }

  // → [new parameter set, {changed: {field: [before, after]}, conflict}]
  function constrain(s0, edited, lh, h, bead) {
    const s = { ...s0 }, before = {};
    for (const k of DIMS) before[k] = s[k] === undefined ? null : s[k];
    migrate(s); defaults(s); fill(s, lh, h, bead); sync(s);
    if (edited === 'width' || edited === 'upper_height') s.driver = edited;
    const drv = (s.driver === 'upper_height' || s.driver === 'height') ? 'upper_height' : 'width';
    // the upper first (a Curved lower's depth rule does not limit it), then the lower deepened
    const p = s.lower === 'arc' ? { ...s, lower: 'pointed' } : s;
    let conflict = null;
    const ev = evaluate(p, lh, h, bead);
    if (ev.reason === 'empty') {
      conflict = +s.width > EPS ? 'the opening has no height: give it a lower profile, a middle or an upper height'
                                : 'the width must be > 0';
    } else if (!ev.ok) {
      if (drv === 'upper_height') {
        const w = maxWidth(p, lh, h, bead, +s.width);
        if (w > 0) s.width = w;
        else conflict = 'no printable opening has this upper height';
      } else {
        const v = minHeight(p, lh, h, bead, +(s.upper_height || 0));
        if (v == null) conflict = `no printable upper height for a ${+s.width} in wide opening`;
        else s.upper_height = v;
      }
      sync(s);
    }
    fitArcDepth(s, lh);
    if (conflict == null && !evaluate(s, lh, h, bead).ok) conflict = 'these dimensions cannot be made printable';
    const changed = {};
    for (const k of DIMS)
      if (before[k] != null && s[k] != null && Math.abs(+before[k] - +s[k]) > 1e-9) changed[k] = [before[k], s[k]];
    return [s, { changed, conflict }];
  }

  function minUpperFixed(W, upper, spring, lh, h, bead) {
    if (W <= EPS) return 0;
    if (h <= 0) return null;
    const wm = wmin(bead, h);
    const ok = n => { const U = clean(n * lh); return !(upper === 'curve' && U <= 0.5 * W + EPS) && upperWorst(W, upper, U, spring, lh, wm) <= h + EPS; };
    let lo = 0, hi = 1;
    while (!ok(hi)) { lo = hi; hi *= 2; if (hi * lh > MAX_DIM) return null; }
    while (hi - lo > 1) { const mid = Math.floor((lo + hi) / 2); if (ok(mid)) hi = mid; else lo = mid; }
    return clean(hi * lh);
  }

  root.OpeningShape = { EPS, Q, UPPERS, LOWERS, HEADER_DEFAULTS, wmin, q16, upperHalf, lowerHalf, migrate,
                        parts, sync, zsnap, snapZ, widthAt, layerWidths, upperWorst, arrayOffsets, springs, evaluate,
                        maxWidth, minHeight, arcMinDepth, fill, defaults, limits, constrain, minUpperFixed };
})(typeof window !== 'undefined' ? window : globalThis);

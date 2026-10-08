"""
Wall-web experiment runner: every fixture × {production, candidates},
metrics, perturbation + cross-Z tests → out/index.html + out/results.json.

    cd design-toolpath/design_proto
    .venv/bin/python experiments/wall_web/run.py            # all
    .venv/bin/python experiments/wall_web/run.py straight   # some cases
"""
from __future__ import annotations

import html
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import HERE, BEAD, CONTACT, SPACING, Corridor, bbox, resample   # noqa: E402
import fixtures as FX          # noqa: E402
import production as PROD      # noqa: E402
import truss as TA             # noqa: E402
import propagate as PB         # noqa: E402
import metrics as MX           # noqa: E402

THETA = 45.0
DENSE_S = 5.5 / math.tan(math.radians(THETA)) + BEAD      # production at A's straight-wall pitch

METHODS = [
    ('prod_zz', 'Production — zigzag (Target Spacing 20)', '#2f5fb3',
     lambda rings, C: PROD.plan(rings, 'zigzag')),
    ('prod_wave', 'Production — wave (Target Spacing 20)', '#4b86d6',
     lambda rings, C: PROD.plan(rings, 'wave')),
    ('prod_dense', f'Production — zigzag at A\'s pitch (S {DENSE_S:.1f})', '#7a8fb8',
     lambda rings, C: PROD.plan(rings, 'zigzag', spacing=DENSE_S)),
    ('A', f'A — adaptive truss phase field (θ {THETA:.0f}°, bond {BEAD:g}, r {0.5 * BEAD:g})', '#1f9d55',
     lambda rings, C: TA.plan(rings, theta=THETA, corridor=C)),
    ('B', f'B — fixed-angle propagation (θ {THETA:.0f}°, bond {BEAD:g}, r {0.5 * BEAD:g})', '#d9822b',
     lambda rings, C: PB.plan(rings, theta=THETA, corridor=C)),
]
MAIN = ['prod_zz', 'A', 'B']


def run_method(key, regions):
    fn = next(m[3] for m in METHODS if m[0] == key)
    polys, reps = [], []
    t = time.time()
    for rings in regions:
        C = Corridor(rings, CONTACT) if key in ('A', 'B') else None
        p, r = fn(rings, C)
        polys += p
        reps.append(r)
    return polys, reps, time.time() - t


def contacts_only(regions, polys):
    F = MX.Faces([r for reg in regions for r in reg])
    return [c[0] for c in MX.contacts_of(F, polys)[0]]


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def svg(regions, polys, vis, colour, width=420, pad=12):
    x0, y0, x1, y1 = bbox([r for reg in regions for r in reg], polys)
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    w, h = x1 - x0, y1 - y0
    H = max(120, min(420, width * h / w))
    out = [f'<svg viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" width="100%" style="max-height:{H:.0f}px" '
           f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">']
    d = ''.join('M' + 'L'.join(f'{p.x:.2f},{p.y:.2f}' for p in r) + 'Z' for reg in regions for r in reg)
    out.append(f'<path d="{d}" fill="var(--wall)" fill-rule="evenodd" stroke="var(--skin)" '
               f'stroke-width="{BEAD}" stroke-opacity="0.45" stroke-linejoin="round"/>')
    out.append(f'<path d="{d}" fill="none" stroke="var(--skinline)" stroke-width="0.5"/>')
    for p in polys:
        if len(p) < 2:
            continue
        pd = 'M' + 'L'.join(f'{q.x:.2f},{q.y:.2f}' for q in p)
        out.append(f'<path d="{pd}" fill="none" stroke="{colour}" stroke-opacity="0.38" stroke-width="{BEAD}" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        out.append(f'<path d="{pd}" fill="none" stroke="{colour}" stroke-width="0.6"/>')
        a = p[0]
        out.append(f'<circle cx="{a.x:.2f}" cy="{a.y:.2f}" r="1.6" fill="none" stroke="{colour}" stroke-width="0.6"/>')
    for q in vis['congested']:
        out.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="0.9" fill="var(--cong)" fill-opacity="0.45"/>')
    for q in vis['contacts']:
        out.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="0.9" fill="var(--ink)"/>')
    for q in vis['sharp']:
        out.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="1.8" fill="none" stroke="var(--sharp)" stroke-width="0.6"/>')
    for q in vis['intrusion']:
        out.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="0.9" fill="var(--intr)"/>')
    for q in vis['out']:
        out.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="1.4" fill="var(--bad)"/>')
    out.append('</svg>')
    return ''.join(out)


def fmt(v):
    if v is None:
        return '—'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        return f'{v:.1f}' if abs(v) < 1000 else f'{v:.0f}'
    return str(v)


def metric_table(m, t):
    rows = [
        ('braces / contacts', f"{m['braces']} / {m['contacts']}"),
        ('brace angle mid-cavity med [min–max]', f"{fmt(m['angle_median'])}° [{fmt(m['angle_min'])}–{fmt(m['angle_max'])}]"),
        ('skin span max / med (in)', f"{fmt(m['skin_span_max'])} / {fmt(m['skin_span_median'])}"),
        ('skin beyond D=40', f"{fmt(m['skin_beyond_D_pct'])} %"),
        ('bond median (in)', fmt(m['bond_median'])),
        ('web length (in)', fmt(m['web_length'])),
        ('kinks ≥20° / max kink', f"{m['kinks']} / {fmt(m['max_kink_deg'])}°"),
        ('min curve radius (in)', fmt(m['min_curve_radius'])),
        ('congested / near-dup (in)', f"{fmt(m['congested_len'])} / {fmt(m['near_duplicate_len'])}"),
        ('out of wall / face-touching (in)', f"{fmt(m['out_of_wall_len'])} / {fmt(m['skin_intrusion_len'])}"),
        ('components / odd nodes', f"{m['components']} / {m['odd_nodes']}"),
        ('Euler circuit · closure extra', f"{fmt(m['eulerian_circuit'])} · {fmt(m['closure_extra_len'])} in"),
        ('time', f'{t * 1000:.0f} ms'),
    ]
    bad = lambda k, v: (k.startswith('out of wall') and not v.startswith('0.0 /'))
    return '<table class="m">' + ''.join(
        f'<tr{" class=bad" if bad(k, v) else ""}><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>'
        for k, v in rows) + '</table>'


# ---------------------------------------------------------------------------

def main(only=()):
    os.makedirs(os.path.join(HERE, 'out'), exist_ok=True)
    results = {'cases': {}, 'perturbations': {}, 'zseries': {}, 'checks': {}}
    cards = []
    for name, desc, fn in FX.CASES:
        if only and name not in only:
            continue
        regions = fn()
        row = []
        results['cases'][name] = {}
        for key, label, colour, _ in METHODS:
            polys, reps, t = run_method(key, regions)
            m, vis = MX.measure(regions, polys)
            m['time_s'] = round(t, 3)
            if key == 'B':
                ev = {}
                for r in reps:
                    for k, v in r['events'].items():
                        ev[k] = ev.get(k, 0) + v
                m['events'] = ev
                m['seeds'] = sum(r['seeds'] for r in reps)
            results['cases'][name][key] = m
            extra = ''
            if key == 'B':
                extra = f"<div class=note>seeds {m['seeds']} · events {html.escape(json.dumps(m['events']))}</div>"
            if key.startswith('prod') and any('fallback' in r for r in reps):
                extra = '<div class=note bad>production: field fallback (not wall-like at this spacing)</div>'
            row.append(f'<div class="card{" minor" if key not in MAIN else ""}"><h4 style="color:{colour}">{html.escape(label)}</h4>'
                       f'{svg(regions, polys, vis, colour)}{extra}{metric_table(m, t)}</div>')
            print(f'{name:14s} {key:10s} braces {m["braces"]:4d} angle {fmt(m["angle_median"]):>5s} '
                  f'span {fmt(m["skin_span_max"]):>6s} out {m["out_of_wall_len"]:5.1f} kinks {m["kinks"]:4d} r {fmt(m["min_curve_radius"]):>5s} '
                  f'comps {m["components"]:3d} odd {m["odd_nodes"]:3d} extra {m["closure_extra_len"]:7.1f}  {t:.2f}s')
        cards.append(f'<section id="{name}"><h3>{html.escape(desc)}</h3><div class="row">{"".join(row)}</div></section>')

    # --- stability -----------------------------------------------------------
    stab_rows = []
    if not only:
        for pname, pdesc, base_fn, pert_fn, site in FX.PERTURBATIONS:
            base, pert = base_fn(), pert_fn()
            res = {}
            for key, label, colour, _ in METHODS:
                pa, _, _ = run_method(key, base)
                pb, _, _ = run_method(key, pert)
                ca, cb = contacts_only(base, pa), contacts_only(pert, pb)
                res[key] = MX.compare(pa, ca, pb, cb, site)
            # A tracked against the unedited plan (hysteresis + pair insertion)
            pa, pb = [], []
            for rb, rp in zip(base, pert):
                p0, rep0 = TA.plan(rb, theta=THETA)
                p1, _ = TA.plan(rp, theta=THETA, track=rep0['track'])
                pa += p0
                pb += p1
            res['A_track'] = MX.compare(pa, contacts_only(base, pa), pb, contacts_only(pert, pb), site)
            results['perturbations'][pname] = res
            stab_rows.append((pdesc, res))

        # --- cross-Z series ------------------------------------------------------
        for zname, series in (('curve', FX.zseries_curve()), ('opening', FX.zseries_opening())):
            zres = {}
            for key in [m[0] for m in METHODS] + ['A_track']:
                counts, steps = [], []
                prev, carried = None, None
                for k, c0, regions in series:
                    if key == 'A_track':
                        # A with CROSS-Z phase tracking, chained layer to layer
                        polys, tracks = [], []
                        for ri, rings in enumerate(regions):
                            p_, rep_ = TA.plan(rings, theta=THETA,
                                               track=(carried[ri] if carried and ri < len(carried) else None))
                            polys += p_
                            tracks.append(rep_['track'])
                        carried = tracks
                    else:
                        polys, _, _ = run_method(key, regions)
                    m, _ = MX.measure(regions, polys)
                    counts.append(m['braces'])
                    # back to the reference frame (similarity inverse); thickness is physical
                    back = [[q.__class__(c0.x + (q.x - c0.x) / k, c0.y + (q.y - c0.y) / k) for q in p] for p in polys]
                    cont = [q.__class__(c0.x + (q.x - c0.x) / k, c0.y + (q.y - c0.y) / k)
                            for q in contacts_only(regions, polys)]
                    if prev is not None:
                        steps.append(MX.compare(prev[0], prev[1], back, cont, None, tol=1.5))
                    prev = (back, cont)
                zres[key] = {'braces': counts, 'steps': steps}
            results['zseries'][zname] = zres

        # --- checks: A reduces to production on a straight wall -------------------
        regions = FX.straight()
        pz, _ = PROD.plan(regions[0], 'zigzag', closed=False)        # one open pass (lone run)
        w = 10.0 - 2 * CONTACT
        th_eq = math.degrees(math.atan(w / SPACING))
        pa, _ = TA.plan(regions[0], theta=th_eq, bond=0.0, r_turn=0.0, d_skin=1e9, a_min=0.0)
        # stations along the wall (x), whichever face each lands on (the start
        # face is a free choice: production picks its own, A the canonical one)
        xa = sorted(q.x for q in contacts_only(regions, pa))
        xz = sorted(q.x for q in contacts_only(regions, pz))
        dx = sorted(min(abs(a - b) for b in xz) for a in xa)
        results['checks']['A_equals_production_on_straight'] = {
            'theta_equivalent': round(th_eq, 2), 'contacts_A': len(xa), 'contacts_production': len(xz),
            'station_offset_median_in': round(dx[len(dx) // 2], 3), 'station_offset_max_in': round(dx[-1], 2),
            'note': 'production single open pass (closed=False); the extra production contacts are its cap V landings'}
        # B grazing on the U bend vs angle
        ub = FX.strong_curve()
        sweep = {}
        for th in (15.0, 25.0, 35.0, 45.0, 60.0):
            polys, rep = PB.plan(ub[0], theta=th)
            m, _ = MX.measure(ub, polys)
            sweep[th] = {'events': rep['events'], 'skin_beyond_D_pct': m['skin_beyond_D_pct'],
                         'skin_span_max': m['skin_span_max'], 'braces': m['braces']}
        results['checks']['B_angle_sweep_ubend'] = sweep

    with open(os.path.join(HERE, 'out', 'results.json'), 'w') as f:
        json.dump(results, f, indent=1, default=str)
    page = render_page(cards, stab_rows, results)
    with open(os.path.join(HERE, 'out', 'index.html'), 'w') as f:
        f.write(page)
    print('wrote', os.path.join(HERE, 'out', 'index.html'))


def render_page(cards, stab_rows, results):
    labels = {m[0]: m[1] for m in METHODS}
    colours = {m[0]: m[2] for m in METHODS}
    st = ''
    if stab_rows:
        keys = [k for k, *_ in METHODS] + ['A_track']
        head = ''.join(f'<th style="color:{colours.get(k, colours["A"])}">{html.escape(k)}</th>' for k in keys)
        body = ''
        for desc, res in stab_rows:
            body += f'<tr><td>{html.escape(desc)}</td>' + ''.join(
                f"<td>{r['far_web_moved_pct']} % web · {r['far_contact_moved_pct']} % contacts · Δn {r['d_contacts']:+d}</td>"
                for r in (res[k] for k in keys)) + '</tr>'
        st = (f'<h2>Stability (small edits)</h2><p>Far field = farther than 40 in from the edit (whole wall for '
              f'global edits). “moved” = farther than 1 in from the edited web / nearest edited contact.</p>'
              f'<table class="t"><tr><th>edit</th>{head}</tr>{body}</table>')
    zs = ''
    if results.get('zseries'):
        for zname, zres in results['zseries'].items():
            rows = ''
            for k in [m[0] for m in METHODS] + ['A_track']:
                r = zres[k]
                steps = ' · '.join(f"{s['far_contact_moved_pct']}%" for s in r['steps'])
                rows += (f'<tr><td style="color:{colours.get(k, colours["A"])}">{html.escape(k)}</td><td>{r["braces"]}</td>'
                         f'<td>{steps}</td></tr>')
            zs += (f'<h3>Cross-Z series: {zname} (scale 1.00 → 1.10 in 2 % steps, wall thickness physical)</h3>'
                   f'<table class="t"><tr><th>method</th><th>braces per layer</th>'
                   f'<th>contacts moved &gt; 1.5 in per step (in the reference frame)</th></tr>{rows}</table>')
    chk = results.get('checks', {})
    ck = ''
    if chk:
        ck = '<h2>Checks</h2><pre>' + html.escape(json.dumps(chk, indent=1, default=str)) + '</pre>'
    legend = ('<p class=legend><b>Legend</b> — beige: wall material; grey band: skin bead (W = 3 in) on the face '
              'centreline; coloured band: web bead; black dots: contacts; open circle: polyline start; '
              '<span style="color:var(--bad)">red</span>: outside the wall; '
              '<span style="color:var(--intr)">orange</span>: web closer to a face than the contact separation (production: intentional exact junction hand-offs / transfer landings); '
              '<span style="color:var(--sharp)">magenta ring</span>: kink (a corner ≥ 20° in the bead path); '
              '<span style="color:var(--cong)">purple</span>: congestion (another bead within 0.9 W).</p>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wall Web Experiment</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#222; --ink:#111; --wall:#efe6d6; --skin:#a8977c; --skinline:#6d5f4b;
  --bad:#d62828; --intr:#f08c00; --sharp:#c2185b; --cong:#6a3fb5; --card:#fff; --line:#e3ded4; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#1b1a18; --fg:#e8e4dc; --ink:#f4f0e8;
  --wall:#3a3428; --skin:#8c7c62; --skinline:#cbb998; --card:#24221f; --line:#3b3833; }} }}
body {{ background:var(--bg); color:var(--fg); font:13px/1.45 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:28px 0 8px; }} h3 {{ font-size:14px; margin:22px 0 8px; }}
h4 {{ font-size:12px; margin:0 0 6px; }}
.row {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:10px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:6px; padding:8px; }}
.card.minor {{ opacity:.82; }}
table.m {{ width:100%; border-collapse:collapse; font-size:11px; margin-top:6px; }}
table.m th {{ text-align:left; font-weight:500; color:#888; padding:1px 4px 1px 0; white-space:nowrap; }}
table.m td {{ text-align:right; padding:1px 0; }}
table.m tr.bad td {{ color:var(--bad); font-weight:600; }}
table.t {{ border-collapse:collapse; font-size:12px; display:block; overflow-x:auto; }}
table.t th, table.t td {{ border:1px solid var(--line); padding:4px 6px; text-align:left; vertical-align:top; }}
.note {{ font-size:11px; color:#888; margin-top:4px; }} .note.bad {{ color:var(--bad); }}
pre {{ font-size:11px; overflow-x:auto; background:var(--card); border:1px solid var(--line); padding:8px; }}
.legend {{ font-size:12px; }}
</style></head><body>
<h1>Wall web experiment</h1>
<p>Structural web between two printed skins: current production lattice vs two research candidates. Physical defaults: bead W 3 in,
contact separation 2.25 in (rails 2.25 in inside each face centreline), 10 in walls unless stated. Experimental harness only
(<code>design_proto/experiments/wall_web</code>); see NOTE.md for the analysis.</p>
{legend}
{''.join(cards)}
{st}
{zs}
{ck}
</body></html>'''


if __name__ == '__main__':
    main(tuple(sys.argv[1:]))

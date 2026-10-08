# Wall web research note: what rule should generate the web between two skins?

**Status: EXPERIMENTAL RESEARCH (2026-10-07).** Nothing here is wired into the Designer.
`wall_lattice.plan()` and all production geometry are unchanged. The harness is
self-contained and can be deleted:

    cd design-toolpath/design_proto
    .venv/bin/python experiments/wall_web/run.py      # ≈ 30 s → out/index.html, out/results.json

| File | Role |
|---|---|
| `common.py` | Path setup and constants. Read-only reuse of `wall_lattice.skeleton` / `_Geo` and `infill._Region`. Contact rails, fillet. |
| `fixtures.py` | Simple walls as rings. Openings, junctions and the reference network go through the real `PrintLayer` pipeline. |
| `production.py` | `wall_lattice.plan`, called exactly as the Designer calls it with physical rules on. |
| `truss.py` | **Candidate A**: adaptive truss phase field. |
| `propagate.py` | **Candidate B**: boundary-driven fixed-angle propagation. |
| `metrics.py` | Geometry-only diagnostics, the web + skins graph, and stability comparison. |
| `run.py` | Runs every case × method, plus the perturbation and cross-Z tests, and renders the HTML. |
| `sheets.py` | PNG comparison sheets for review: `out/01_straight.png` … `out/10_cross_z_tracking.png` (≈ 10 s, macOS `qlmanage`). |

Physical defaults throughout: bead W = 3 in, contact separation 2.25 in, 10 in walls.
So the cavity a brace crosses, rail to rail, is **w = 10 − 2·2.25 = 5.5 in**.

**Physical objective (clarified by the designer, 2026-10-07).** The goal is a continuous structural
web inside the wall that keeps BOTH skins adequately bonded and supported. Strict alternation
between skins is NOT required:
- Cross-wall braces remain the normal mechanism.
- Same-skin contacts are allowed where they help. Around a concave or tight corner, for example, outer → inner → inner → outer can beat forcing an immediate brace, and the same-skin pair can form a loop, teardrop or local brace.
- The evaluation rule is **neither skin may exceed its maximum unsupported longitudinal span (D)**, not "contacts must alternate". The harness metric `skin_beyond_D_pct` (per-face arc gaps between contacts) already measures exactly that, so no rerun was needed. Sections 3, 10, 11 and 12 below are revised accordingly.

---

## 1. What the production method actually does

`wall_lattice.plan` is not a sine wave. It works in these steps:

1. **Skeleton.** A chordal axis over a constrained Delaunay triangulation of the wall rings.
   - It splits the wall into **runs**, which are sleeve chains whose every chord spans face to face. That gives exact face correspondence, on curves too.
   - It also finds dead **ends** and junction **clusters**.
2. **Passes per run** are chosen network-wide by route inspection (Chinese postman on the skeleton):
   - an ordinary run gets one pass;
   - a ring gets a circulating pass;
   - a dead end or a lone run gets an **out-and-back** of two mirrored phases joined by a **cap V**;
   - a run crossed by a lineage jamb is doubled.
3. **Stations.** Each run is cut into **segments** between fixed anchors: junction ends, dead-end spans and **corners**. Corners get a **corner brace**, meaning both corner points are landed.
   - A segment of usable length U gets n = round(U/S) crossings, evenly spaced in a parameter u. The parameter u is weighted towards the shorter face, so the inside of a bend is not crowded.
   - The **maximum unsupported distance** (1.375·S) forces extra crossings where needed.
   - In other words, a **two-point boundary-value problem per segment**.
4. **Junctions.**
   - Pass ends meet on shared junction corner points.
   - Start sides and count parities are chosen jointly with the corner pairing.
   - Transfer landings keep the region one closed route.
5. **Stitch geometry.** Each stitch is built in strip coordinates: it interpolates between the two **contact rails** along the run.
   - Zigzag gives straight-ish stitches with a sharp V at each landing.
   - Wave gives a half-sine tangent to both rails.
   - Fallbacks, in order: Hermite → straight → dogleg.
6. **Lineage jambs** (shared scaffold across Layer Designs), **cross-Z tracking** (counts, sides, seams and pairings carried from the reference plan), and the **bead / no-cavity** rule.

The architecture is already wall geometry → web geometry. The only waveform-like input is the
**Target Spacing S**, and it plays three roles:
- the crossing pitch;
- the maximum-unsupported default;
- the **wall / area classifier**: a region is a wall only if thickness ≤ 1.6·S.

## 2. Truss geometry: is brace angle more fundamental than wavelength?

For parallel skins with cavity w and brace angle θ to the wall direction, one crossing advances

    a = w·cot θ + b            (b = bond length: the web runs along the skin for b at each contact)

The per-skin span (the distance between supports on one face) is 2a for a single pass.

**Finding 1: on a straight constant-width wall, angle and spacing are the same parameter.**
- At the production default S = 20, the braces sit at **θ = atan(5.5/20) ≈ 15–16°**. The measured mid-cavity median is 15.0–16.5° on every fixture.
- A Warren truss is at 45–60°, so today's default web is a very flat zigzag.
- Candidate A at the equivalent θ = 15.38° with b = 0 reproduces production's stations. Both have 14 crossings on the straight wall, and A's 17 contacts sit a median **0.057 in** from production's 19 (the 2 extra are production's cap V landings).

**Finding 2: steeper braces cost much less material than expected.**
- Brace length per crossing is w/sin θ, so shallow braces are long.
- On the straight wall, A at 45° with a 3 in bond places 34 braces with a per-skin span of 19.8 in. Its single pass is 345 in of web.
- Production's lone-wall web is 580 in, but that is a closed out-and-back (two passes). Per pass the two are comparable.

**Finding 3: angle and spacing differ as soon as width varies, and there angle is the better primitive.**
- Holding θ fixed makes the pitch scale with local width, so the truss stays geometrically self-similar.
- Holding S fixed makes the angle vary: on production's taper case, atan(w/S) runs from ≈ 4° at the 6 in end (an almost flat web) to ≈ 34° at the 18 in end; the measured median is 23°.
- But the per-skin span is an absolute length (it relates to the bead sagging or buckling between supports, not to wall width). The structural rule is therefore **θ preferred, clamped by an absolute maximum skin span D, and floored by a congestion limit a_min**:

      a(s) = clamp( w(s)·cot θ + b ,  a_min / inner-face speed ,  D / (2·outer-face speed) )

**Finding 4: S being the wall / area classifier blocks steep webs.**
- WIDE = 1.6 means a 10 in wall must have S ≥ 6.25. That caps braces at ≈ 41°, whatever the designer wants.
- The production run at A's pitch (S = 8.5, "prod_dense") still works on 10 in walls, but the coupling should go: classify walls geometrically (medial-axis width vs bead), not by spacing.

**On curved walls, define θ against the centre-line (skeleton) tangent in strip coordinates.**
- The physical angle then differs slightly between the inner and outer skins: steeper inside, shallower outside.
- That is the right compromise. The skin-span limit is checked on the outer face (longest span) and the congestion floor on the inner face.
- A at 45° measures a 45.1–46.6° median on the curve, the U bend, the taper and the corner.

**Near corners and junctions,** the angle is not meaningful: it is a local transition. Keep production's anchor treatment (a corner brace, junction corner hand-offs). That is where the planned "motif vocabulary" belongs.

## 3. Billiards / ray propagation, tested properly

**Candidate B** is the purest local rule, a structural billiard:
- land at the contact separation;
- run along the face for b;
- leave at a fixed angle θ to the local face tangent, keeping the longitudinal sense;
- no skeleton (it is used only for seeding).

This is a *fixed reflection law*, the extreme member of the "contracting reflection law" family. Those are called pinball billiards; their θ → normal limit is the slap map.

**Mathematics that actually applies:**
- **Contracting laws destroy area preservation.**
  - Pinball billiards are dissipative.
  - They have dominated splitting [Markarian–Pujals–Sambarino].
  - They converge to attractors, including chaotic ones in focusing tables [Arroyo–Markarian–Sanders].
  - Polygonal tables have hyperbolic attractors with SRB measures [Del Magno et al.].
  - A fixed angle is the limit where the outgoing angle carries no memory, so the dynamics reduce to a 1-D map of landing positions.
- **Straight corridor: that 1-D map is a translation (derivative 1).** A position error is never amplified, but it is never damped either. A local boundary change shifts *every downstream landing* permanently. That is global re-phasing by construction.
- **Literal specular billiards** in a slowly varying corridor conserve the transverse action w·sin φ (adiabatic invariant):
  - the angle steepens as the corridor narrows;
  - it can reverse the ray (a "magnetic mirror");
  - it flattens as the corridor widens.
  - So specular reflection is worse than a fixed angle.
- **Whispering gallery (straight rays in a curved corridor).**
  - A ray leaving the outer rail (radius R_o) at angle θ to the tangent reaches the inner rail (R_i) only if **cos θ < R_i / R_o**. Otherwise it grazes past and lands on the outer skin again.
  - On the U bend (rails at R 42.25 / 47.75): θ_c ≈ 27.8°. The measured sweep agrees: at 15° and 25° there are 14 and 2 same-skin bounces (31.5 % / 17.7 % of skin left beyond D); from 35° up there are none.
  - **A same-skin bounce is not a defect in itself** (see the physical objective above). Two kinds need telling apart:
    - **(A) Useful local same-skin contact.** A short loop, teardrop or brace that adds support where an immediate crossing is awkward (a concave corner, tight curvature, a junction), after which the web returns to the other skin well within D.
    - **(B) Pathological grazing ("whispering gallery").** Consecutive landings stay on one skin because the ray cannot reach the other, so the opposite skin is neglected beyond D.
  - The test between them is the span rule, not the event count. In the sweep, the 15° and 25° same-skin events coincide with the inner skin left unsupported for 163 in and 118 in (D = 40), so they are class B. B never placed same-skin contacts at 45° on any fixture, and its 45° failure on the reference network (14.9 % of skin beyond D) comes from rays taking one branch at junctions, not from same-skin contacts.
  - What B lacks is not permission for same-skin contacts but *intent*: it cannot choose a same-skin loop where one would help, and it cannot stop grazing where it hurts.
  - At production-like angles (≈ 15°), any bend tighter than R_o ≈ 160 in would fail.
  - The cure is to propagate in **strip coordinates** (curved rays that follow the wall). That is what production and A already do, and it is the useful part of the analogy.
- **Termination is the hard part.** A run ends at an arbitrary phase.
  - At a dead end, B's ray turns round on the cap and returns. It returns either interleaved (an emergent mirrored X-lattice, often quite handsome: one opening, the corner) or onto its own landings (it merges and stops).
  - Which of the two happens depends on the accumulated phase, so it flips under tiny edits.

**Measured instability of B:**
- A 0.5–1 in local bump on one face of a straight wall removes **31 contacts** and moves **73 %** of far-field contacts. The return path changed from interleaved to merged.
- Moving an opening 2 in moves **100 %** of contacts.
- Through Z, the brace counts run 35, 34, **68**, 36, 36, **72** (curve) and 132, 78, 72, **141**, 72, 70 (opening), with 54–100 % of contacts moving per step.
- At junctions, rays take one branch: on the reference network, 14.9 % of skin is left beyond D, with a 170 in maximum span and 7 components.

**Verdict:** boundary-driven propagation is deterministic and local *in time*, but it is an initial-value process. Its sensitivity is not chaotic, but it is *neutral* (no damping) plus *discontinuous* at terminations. That is exactly the "small opening change re-phases the whole wall" behaviour to avoid. The fix is to pin the phase at anchors, which turns it back into a boundary-value problem, i.e. Candidate A.

## 4. Diagrid / multi-layer thinking

**An alternating ±θ per layer is not printable for stacked mud beads.**
- Layer k+1's brace would sit on layer k's mirrored brace only near their crossing point.
- The rest overhangs by up to a full pitch, against a 1.5 in Maximum Overhang.
- Vertical continuity forces the web to be **nearly identical layer to layer**: vertical web plates, the opposite of a diagrid.

**A diagrid can emerge only as a slow phase drift.**
- Shift the web by δ ≤ max overhang per layer, so the web plates lean in elevation.
- With phase as a field, that is **one scalar per layer**: φ_offset(z), with |Δφ| per layer ≤ overhang / pitch.

**This does change the 2D primitive:** the web should be defined by a **phase** along each run, with integer phases = contacts, not by waveform samples. Then:
- cross-Z identity is "contact k";
- a diagrid is "phase drifts with z";
- count changes are explicit topological events (§7).

## 5. Curved and variable-width walls (measured, 45° for A / B)

| case | production zigzag S 20 | A | B |
|---|---|---|---|
| gentle curve | 30 braces, 15°, span 21 in | 34 braces, 45.5°, span 20 in | 35 braces, 45°, span 22 in |
| U bend (inner R 40) | 36, 15°, span 24 | 41, 45.1°, span 20, no crowding | 42, 45° (fails below 28°) |
| taper 6→18 in | 25, angle varies widely (≈ 4° → 34°, median 23°) | 27, **constant ≈ 46°**, pitch ∝ width | 25, 45°, span 39 (wide end) |
| sharp corner | 36, corner brace | 38, corner anchored on the inner corner | 75 (emergent X out-and-back) |
| short 36 in | 8, 29° | 4, 41° (anchors dominate) | 6, 45°, near-duplicate 58 in |

- All methods stay inside the wall: 0 in out-of-wall in every case.
- Production zigzag has a **sharp V kink at every landing**: 31 on the straight wall, 91 on the reference network.
- A's fillets remove them: 1–8 kinks per case, at junction hand-offs and cap legs.
- A, B and production all need a medial axis or a correspondence for curves. B avoids it only by accepting whispering-gallery failure at shallow angles.

## 6. Openings as geometry

In production an opening already *is* geometry:
- it cuts the material;
- the caps become run ends;
- anchors bound the influence.

The measured locality of moving an opening 2 in is:

| method | web moved | contacts moved | contact change |
|---|---|---|---|
| production | 0 % | 8 % (far field) | — |
| A | 10 % | 15 % | 1 |
| A tracked against the unedited plan | 2.8 % | — | 0 |
| B | 46 % | 100 % | — |

So the gain does not come from a new web primitive. It comes from:
- anchors (keep);
- **per-segment canonical faces + corner braces** (an odd count change in one segment must not swap the faces of the next — found in this experiment; production gets this from its corner braces);
- **hysteresis + pair insertion** when a count must change.

Treating the opening boundary as a reflecting obstacle (B) is what causes non-locality. Headers remain an Assembly concern.

## 7. Cross-Z stability

Six "layers": scale 1.00 → 1.10 in 2 % steps, thickness physical. The table gives the fraction of contacts moving more than 1.5 in per step, in the reference frame.

| method | curve: braces / moved per step | ring with opening: braces / moved per step |
|---|---|---|
| production zigzag (untracked) | 30 32 32 32 32 34 / **83**, 6, 6, 0, **88** % | 58 58 64 64 64 64 / 0, **51**, 3, 0, 0 % |
| A plain | 34 34 35 36 36 37 / 3, **80**, **81**, 3, **81** % | 68 66 66 65 68 78 / **57**, 2, 34, 29, 52 % |
| **A tracked** (hysteresis ± pair, carried normalised phases, 5 % relaxation per layer) | 34 34 34 36 36 36 / 3, 3, **11**, 3, 0 % (median shift 0.06–0.45 in) | 68 68 68 68 78 78 / 1, 1, 1, **22**, 0 % (median 0.24–0.40 in) |
| B | 35 34 68 36 36 72 / 95, 100, 71, 95, 78 % | 132 78 72 141 72 70 / 71–98 % |

Production in the real pipeline tracks counts against the untransformed reference, so its untracked rows overstate production's Z behaviour. They show why tracking is needed.

**Lessons:**
1. **Any uniform two-point distribution re-phases a whole segment when its count changes.** That covers production and plain A.
2. **In a single pass, crossings must come and go in pairs.** One extra crossing swaps the face of every later landing.
3. **Run direction and face labels from the skeleton are arbitrary and flip between layers.** Production's `_match_track` solves this. The prototype needs a canonical direction and left face.
4. **Tracked phases with local pair insertion keep contacts within ≈ 0.4 in per layer.** That is well inside the 1.5 in overhang, and it is easier to track than the current station layout, because identity is simply "phase k of segment j".

## 8. Structural / biological analogues — resemblance vs generator

| analogue | visual | usable generator? |
|---|---|---|
| Warren truss / lattice girder | yes | **Yes**: θ, b, D, a_min → a(s). Basis of A. |
| Diagrid | yes (in elevation) | Only as a slow phase drift across Z (§4). Not as alternating ±θ. |
| Billiards / ray tracing | yes | Analysis only (whispering gallery, invariants). As a generator, initial-value instability (§3). |
| Streamlines of a field between skins | yes | Equivalent to A if the field is the strip coordinate. A general vector field needs a global solve and gives no integer contacts. |
| Principal-stress trajectories / Michell trusses | yes | Need loads and supports the Designer does not have. Global. Michell's optimum is an orthogonal net: ±45° to principal stresses, i.e. **45° bracing is the shear-optimal Warren angle**, a reason for θ ≈ 45° in pure shear. |
| Topology optimisation | yes | Too global, expensive and load-dependent for routine walls. Maybe offline to calibrate θ / D later. |
| Trabecular bone / cholla | yes | Adaptive density; no deterministic printable single-path rule. Inspiration for "density ∝ demand", which A already expresses through a(s). |
| Gridshells | partial | Different problem (surface form-finding). |

## 9. Gyroid / 3D lattice (separate, long-term)

A coherent 3D web (e.g. TPMS-like) would give X / diamond bracing and smooth evolution through Z. But:
- each mud layer must sit on the one below (overhang ≤ 1.5 in);
- each layer must be one closed route.

The phase-field formulation is the 2D slice that keeps that door open: φ(s, z) with bounded ∂φ/∂z *is* a sampled 3D web. Not implemented; not recommended before the 2D primitive is settled.

## 10. Comparison matrix

| criterion | production (stations in u, zigzag / wave) | A: adaptive truss phase field | B: fixed-angle propagation |
|---|---|---|---|
| straight-wall quality | good, but flat (≈ 15°) at the default S | truss-like; equals production at θ_eq | good locally |
| curved walls | good (strip coordinates) | good (same correspondence) | fails below θ_c (whispering gallery) |
| variable width | angle drifts with width | **constant angle, pitch ∝ width** | constant angle, span grows |
| corners | corner brace (good) | anchor + brace when faces disagree; no same-skin loops yet | emergent; uncontrolled |
| same-skin contacts | only as corner brace / cap V | not yet (strict alternation inside a segment) | uncontrolled: useful or grazing by accident |
| both skins within D | yes, except junction-heavy cases (3–3.7 % beyond D on the T junction / reference) | yes on every fixture | fails on bends below θ_c and at junctions |
| openings | anchors (local) | anchors (local); per-segment faces needed | non-local |
| junctions | rich hand-off machinery | reuses production's (raw prototype: pass ends on corners) | takes one branch; leaves skin uncovered |
| locality of change | segment-bounded | segment-bounded | **global** |
| sensitivity | discrete count flips | the same, cured by hysteresis + pairs | discontinuous at terminations |
| deterministic | yes | yes | yes |
| parameter intuitiveness | S (also the classifier) | θ, bond, turn radius, D, a_min | θ, bond |
| skin contact | point V (kink) | **flat bond b, filleted** | flat bond |
| unsupported-span control | S, 1.375·S | explicit D (outer face) | none (emergent) |
| turn quality | zigzag: kink every landing; wave: smooth | fillet radius r | fillet radius r |
| graph / routeability | closed by construction (doubling, pairing) | raw single pass: needs production's route layer | sometimes closes by accident |
| cross-Z correspondence | tracked counts (works; junctions are the open issue) | **phase identity; tracked: ≤ 0.45 in per layer** | none |
| cost | 0.03–0.3 s per region | 0.02–0.55 s (prototype, unoptimised) | 0.01–0.1 s |
| integration difficulty | — | **low**: replaces station placement + stitch shape inside existing runs | high, and not worth it |
| reuses skeleton / runs | — | yes | no (seeding only) |

## 11. Recommendation

**Keep:** wall-material extraction, the chordal-axis skeleton, runs / ends / junction clusters, anchors (junction ends, dead-end spans, corners, jambs), route-aware pass multiplicity and junction pairing, lineage scaffold, cross-Z matching.

**Revised direction (given the clarified objective): a two-level hybrid.**

    anchored adaptive truss / phase field  = the GLOBAL organising rule
                                            (density, contact identity, locality, cross-Z)
      +
    a LOCAL motif vocabulary               = loops / teardrops / same-skin braces /
                                             diamonds at corners, tight curvature,
                                             junctions, dead ends, jambs and other transitions

Each level does what the experiment showed it is good at:
- **The phase field supplies the structure.** It sets the density from θ, b and D, supplies contact identity (phase k of segment j), keeps changes segment-local, and stays coherent through Z (hysteresis, pair insertion). Strict alternation is the right *default* inside ordinary segments: it is the simplest way to meet the two-skin span rule on a straight or gently curved wall.
- **Motifs are local replacements at anchors.** A motif replaces the phase-field web only in a small window around an anchor or a high-curvature stretch. It may use same-skin contacts (outer → inner → inner → outer around a concave corner, a teardrop at a dead end, a diamond at a junction). It must:
  1. enter and leave at the phase field's boundary contacts, so nothing outside the window changes and contact identity is preserved;
  2. keep both skins within D (the governing rule, replacing strict alternation);
  3. stay inside the wall, respect the turn radius and contact separation, avoid exact retrace and congestion;
  4. preserve the window's route parity (or change it in a way the route layer has already accounted for).
- **The motif is chosen by evaluation, not by user style.** Generate a few valid candidates per window and score them on support (both-skin spans), turn quality, congestion and continuity, as the memory's "motif vocabulary" open question already proposes. Production's corner brace and cap V, A's corner brace, and the designer's loop / teardrop and diamond references become members of the vocabulary.
- **Why this is better than either alone.** The phase field alone forces a crossing even where a same-skin loop would bond the corner better. Motifs alone (or B's emergent behaviour) have no global density, identity or locality. B showed the motifs must be *placed*, not emerge from propagation.

**Phase-field details (Candidate A, unchanged):**
1. **The crossing density comes from structure, not wavelength:** a(s) = clamp(w(s)·cot θ + b, a_min, D). θ is the designer's primary parameter, about 35–50° (45° is shear-optimal). Target Spacing becomes a derived readout.
2. **Contact geometry: a flat bond b along the skin and a minimum turning radius r** (filleted bead path) instead of a point V. That gives better skin bonding, no kinks, and no V-apex congestion.
3. **Stations as a phase field per segment**, integer phase = contact. Each segment starts on its canonical face; where neighbours disagree, the anchor gets a junction motif. A corner brace is the simplest one; a same-skin loop is an alternative.
4. **Topology events explicit:** count hysteresis, changes only in pairs, local insertion at the widest gap, slow relaxation. The same mechanism serves interactive edits (track against the pre-edit plan) and cross-Z layers.
5. **Decouple the wall / area classifier from spacing:** use geometric width vs bead.

**Reject B as a generator.** Keep its analysis: the whispering-gallery threshold, phase propagation being neutral and unanchored, and the useful-vs-pathological same-skin distinction.

**Integration path, if adopted (each its own reviewed pass; NOT started):**
- (a) Replace `seg_options` / `seg_stations` with phase-field stations, θ_eq mode first so geometry is unchanged.
- (b) Add bond + fillet as a stitch construction.
- (c) Expose θ / b / r / D.
- (d) Move tracking to phase identity with pair insertion. That may also help the documented transformed-junction problem, since junction runs would carry phases instead of re-choosing stitch constructions.
- (e) Motif windows at anchors: an interface (boundary contacts in, boundary contacts out, span / parity contract), first with the existing corner brace / cap V, then loop / teardrop / same-skin brace candidates scored on the both-skin span rule.

## 12. Limits of this experiment / open questions

- The candidates produce raw single passes. Doubling, junction pairing, transfers and closure come from production's route layer and were not re-implemented. The routing metrics (odd nodes, closure extra ≈ wall length for lone walls) show what that layer must add.
- Junction transitions in A are naive: pass ends on the shared corner points. The motif vocabulary (loop / teardrop, diamond, same-skin brace) is untouched. Neither candidate generates *intentional* same-skin contacts, so the hybrid in §11 is a proposal backed by the analysis, not a measured result.
- Same-skin events are counted only for B (`events.same_skin`). The span rule (`skin_beyond_D_pct`, `skin_span_max`) is the governing metric for every method. A per-window "both skins within D" check would be needed to evaluate motifs.
- Machine values are unknown: the right θ, bond, turn radius and D for real mud. A's 45° / 3 in / 1.5 in / 40 in are placeholders.
- Contact detection in `metrics.py` is geometric (within contact + 0.6 in of a face). Production's mirrored-pass crossings count as congestion, which is real, since two beads cross there.
- The cross-Z test uses one-run fixtures and matches runs by index. General run identity is production's `_match_track`.

## References (billiards)

- R. Markarian, E. Pujals, M. Sambarino, *Pinball billiards with dominated splitting*, Ergodic Theory Dynam. Systems (2010).
- A. Arroyo, R. Markarian, D. P. Sanders, *Bifurcations of periodic and chaotic attractors in pinball billiards with focusing boundaries*, Nonlinearity (2009) — arXiv:0902.1563.
- G. Del Magno, J. P. Gaivão, J. Lopes Dias, P. Duarte, M. Pinheiro et al., *Chaos in the square billiard with a modified reflection law*, Chaos (2012) — arXiv:1112.1753; *SRB measures for polygonal billiards with contracting reflection laws*, Comm. Math. Phys. (2014) — arXiv:1302.1462.
- Slap maps (θ → normal limit): *Hyperbolic polygonal billiards close to 1-dimensional piecewise expanding maps* — arXiv:1501.03697.
- A. G. M. Michell, *The limits of economy of material in frame-structures* (1904): orthogonal ±45° nets in pure shear.

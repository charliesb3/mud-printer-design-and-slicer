# Wall / Junction / Construction Architecture — FINAL

**Status:** APPROVED (revision 4, 2026-10-15). **M0–M4 implemented 2026-10-15, manually reviewed (with the correction pass) and checkpointed (`b4ccd16`); golden re-baselined (full hashes) at the checkpoint. M5 implemented 2026-10-15, manually reviewed and checkpointed (`dce9366`). M6 + M7 (with the through-wall / Chained Loop correction pass) implemented 2026-10-16, manually reviewed and checkpointed; M8 / M9 and full-wall junction work not started.** Revision 4 records the user's FINAL decisions (§F), which supersede the corresponding parts of §5, §6, §9 and §12. **Revision 5 (2026-10-17) adds §Z — CONSTRUCTION THROUGH Z, the approved architecture for cross-Z coherence and full-wall junctions (invariants, direction, superseded mechanisms, staged plan Z0–Z8). Z0 (green baseline), Z1 (coherence harness) and Z2 (construction identity, byte-identical geometry) done 2026-10-17 — Z.7; Z3+ not started.**

## §Z. CONSTRUCTION THROUGH Z — APPROVED ARCHITECTURE (2026-10-17)

Approved by the user on 2026-10-17 as the architectural direction for cross-Z Wall System coherence AND full-wall junctions (one model, not two). Source: the user's intent statement (§Z.1) and the code analysis of the same day (§Z.2–Z.4). Read §Z before any cross-Z, lattice-tracking, Wall System phase or junction-construction work.

**Reading rule for this document:** §Z is CURRENT. The earlier cross-Z mechanisms it names in §Z.3 are SUPERSEDED as decisions but still in the code until their stage removes them — their implementation notes elsewhere in this file and in the two PROJECT_MEMORY files are history, kept for understanding, not direction.

**Golden / test rule (user, 2026-10-17):** preservation of current golden geometry is NOT a goal in itself. Intentional geometry changes are acceptable when they result from replacing obsolete or over-complicated machinery with a cleaner implementation of the invariants below; unexplained regressions are not. Never modify production geometry merely to make an obsolete test pass. What is preserved is the PRODUCT INVARIANTS (§Z.1), not superseded implementation behaviour.

### Z.1 Durable product / architectural invariants (what any implementation must satisfy)

1. **One continuous evolving construction.** A printed wall is ONE physical structure through Z — never independently solved 2D layers, never independent source shapes patched together afterwards. Architecture (Base / Variants + Assembly transforms, openings, cuts, walls meeting or separating) defines the available TERRITORY per layer; Wall Systems decide how that territory is physically constructed.
2. **Physical support from below is the primary cross-Z requirement.** Every layer's construction is supported by the construction below it as far as geometry allows. The bead-level support checker is the authority and is never weakened to hide findings.
3. **Priority order through Z:** (1) physical support from below; (2) ancestry / continuity / correspondence; (3) local structural character; (4) ideal pattern parameters (Target Web Angle, pitch, period, symmetry, equal spacing). Pattern parameters are preferences, never reasons to break stacking — a nominal 20° web may run at 17° for some layers if support needs it.
4. **Shrinking territory consumes construction LOCALLY at the moving boundary** (a corner, junction, opening, wall end, cut face or other topology boundary). Four lattice cells becoming three is the END cell being progressively consumed; the surviving cells never redistribute globally.
5. **Expanding territory grows out of surviving, supported construction.** New construction branches from what exists below; it never restarts at an unrelated phase. A short brace left between openings is a descendant of the larger web below it, and the web grows back out of it above.
6. **Junctions are LOCAL topology events.** Walls meeting, separating or changing relationship through Z changes only construction near that relationship. Unaffected construction continues as the same evolving construction. A junction that ceases simply ends; the walls continue above as separate constructions (§F1 stands). Nothing is regenerated globally because of a junction.
7. **Same-system full walls form continuous construction DOMAINS across source boundaries.** A circle joined to a rectangle, both in one Interleaved / Linked / Chained Loop / Skin + Web system, is one connected territory — not "circle pattern + rectangle pattern + patch".
8. **Construction ROLE survives joining.** A THIN Out-and-Back wall stays an excursion when it meets a full Wall System; it is never absorbed into Skin + Web, Interleaved, etc. The M7 rule stands: the continuous host strand the branch intercepts diverts out, returns and continues as itself (§F3: host resolved geometrically per layer).
9. **Different Wall Systems remain themselves and meet through LOCAL junction motifs / transitions.** The junction mediates locally; neither system is regenerated because of the other. A junction may use its own simple local construction motif.
10. **Simple parallel-lane transitions are a valid canonical motif.** A patterned system may resolve into parallel lines approaching / through a junction or difficult corner, those lines wrap / connect cleanly, and the pattern resumes afterwards (Interleaved already does this at corners). One motif among possible ones — never a requirement that every junction use it, never an attempt to blend two whole systems mathematically.
11. **Smooth curves remain curves.** Tight curvature alone is never a corner; a circle has zero corners at any radius; on a curve the concave skin owns Target Web Angle and the convex landing adapts (Pass A). Real corners may use local motifs. (Whether a sufficiently rounded architectural corner is best treated as a curve is an open product rule, not decided here.)
12. **Authoring alignment never defines physical connectivity.** Inside / Center / Outside are authoring references. A wall must be able to connect to EITHER physical face of another wall whatever reference its host was drawn from; connectivity belongs to the physical / canonical wall. (Not yet fully satisfied — stage Z7.)
13. **Physical bead / contact rules govern construction and routing.** Wall Thickness = finished deposited width; Bead Width = one pass; Contact Overlap governs intentional landings (never ordinary crossings); Parallel Wall Overlap is the MAXIMUM overlap of neighbouring parallel beads. Routing follows the declared construction (strand identity, join groups) and never infers topology from geometry afterwards.

### Z.2 Current implementation direction (approved 2026-10-17; the ONE architecture)

**Identity-anchored construction, evaluated per layer, with a small construction STATE chained bottom-up.** Not a whole-stack solve (it would destroy the variant cache and the parallel worker pool, invalidate on every edit, and the problem is local in Z); not the superseded "track each layer against the untransformed design by geometric matching" (§Z.3).

Why: the cross-Z problem is mostly an IDENTITY problem. The reverted Pass B (2026-10-16) recovered identity by nearest-distance station matching and failed; identity must never be recovered geometrically. Since M4–M7 every anchor a construction pins to HAS a stable identity (walls, junction records, openings, cut profiles, wall ends); the tracking machinery predates them.

Components, in order:

1. **Stable architectural anchors / identities (per resolved layer; architecture, cheap).** The anchor list of a layer: own corners by (wall id, corner ordinal); junction kinks / ends by junction record id (`junctions.observe` + registry matching, already per layer in `layer_presence`); opening jambs by opening id; cut faces by profile id; wall ends by (wall id, end). Production junction corners are keyed by junction id (today: face pair + XY ordinal). A junction appearing / ceasing on a layer is an event in this list and nowhere else. Junction identity is established BEFORE stack construction.
2. **Construction SEGMENTS between identified anchors.** A same-system DOMAIN = the merged material region whose bounding walls share one Wall System key, generated on the merged skeleton (source boundaries irrelevant, invariant 7); the kinks where member walls meet are anchors with junction identity, treated like corners of the merged outline. THIN walls never join a domain (invariant 8). Mixed-system regions split by wall as today (each part a domain; the mouth chords are junction anchors). Segment identity = (domain system, ordered anchor id pair).
3. **Cheap per-layer TOPOLOGY pass (parallel).** Per layer: the architecture (already resolved by the envelope stage) + skeleton runs segmented by anchors, with segment lengths and anchor arc positions. No geometry generated.
4. **Small sequential bottom-up CONSTRUCTION-STATE chain (trivial cost).** Per domain, per segment id: {station / period count, which end holds the slack, pass sides / parity, junction pairings, seam}; per junction id: {motif kind, host strand ids}; per event: {layer born, ramp}. Carried from the layer below by identity; changed ONLY when physically impossible (gap beyond the maximum unsupported distance, pitch floor, parity / closure) or at a topology event; changed LOCALLY; never redistributed. The chain carries discrete choices, never geometry. Bottom-up because support comes from below; an unanchored initial-value propagation never damps (wall-web research note §3, §7), so every segment is pinned at identified anchors each layer — a boundary-value problem per layer, nothing accumulates. Look-ahead is limited to what the Assembly already supplies (the opening transition ramps, `profiles._transition_layers`).
5. **Parallel GEOMETRY generation from the state (today's build with the state injected, as `lattice_track` / `wall_system_reference` are injected now).** Stations pinned at both anchors at NOMINAL pitch / period; ONE slack cell per segment absorbs length change; the slack sits beside the anchor that moved since the layer below, else where it was below. Waves / chains: period count and phase pinned at anchors, the residue absorbed in a slack zone by the existing fade-to-lanes machinery (invariant 10) instead of stretching the whole span's period. Parallel Walls: stable seam zone.
6. **Local slack / birth / death.** A cell is born or dies only in the slack, by hysteresis; nothing else moves. A new segment created by a split (an opening or junction starting) INHERITS the stations of the segment it was carved from, by clipping (the existing `_clip_to_rings` + `_join_cut_ends` primitive, promoted from Assembly-cut special case to the general rule); a merge (an opening closing) inherits both sides and the former gap becomes the slack. This realises invariants 4 and 5.
7. **Junction motifs owned by stable junction identity.** The junction record owns one local motif per layer range, carried by identity through Z, geometry resolved per layer: the incoming pattern fades into parallel lanes inside the junction zone (existing end-lane machinery), the lanes cross / wrap into the host's printed material (existing push / mouth / staple constructions), the pattern resumes; the host is never regenerated. The pass-10 face-following, yield, push-reach, staple and `~cross` / `~mouth` constructions are the current motif IMPLEMENTATIONS and stay as such. Out-and-Back: M7 unchanged (§F3); strand-id hysteresis only if the harness shows flicker (would amend §F3 — a user decision).
8. **Eventual removal of superseded geometric tracking / reconciliation** (§Z.3) once Z4–Z6 cover their cases, byte-identical at that step.

**Contract change (decision 2026-10-17, supersedes the layer_assembly memory's "no cross-Z hints in the contract"):** the Designer–Assembly build request gains a CONSTRUCTION STATE input (Z4). The Designer still knows nothing of Z: it receives a state, not a height. The Assembly (owner of layer order) runs the chain and passes each layer's state. Wall Systems stay deferred after Assembly (explicit Generate); the chain lives inside Generate.

### Z.3 SUPERSEDED cross-Z decisions and mechanisms (history, not direction)

Marked superseded on 2026-10-17 by §Z.2. Their notes remain where they are (Designer memory → "Z PHASE — CURRENT ARCHITECTURE" E and "Transformed-junction lattice"; layer_assembly memory → contract, "Assembly Profiles", History) for understanding; do not extend them.

| Superseded decision / mechanism | Where it lives today | Replaced by |
|---|---|---|
| "Lattice stations / identity are carried from the UNTRANSFORMED reference plan (layer 0), not from the layer below" (2026-10-06) — `lattice_track`, `_track_map` / `_TRACK_MAPS`, `wall_lattice._match_track` (geometric run matching within ½ thickness), `_tracked_counts`, partial tracking + parity repair | `layer_design.build` / `scaffold`, `wall_lattice.plan(track=…)` | identity-carried state from the layer below (Z.2 §4–5); counts change only locally in the slack |
| "One shared lattice per lineage: a lattice FAMILY prints ONE scaffold planned on the generator with all openings removed, clipped to each member; a member that cannot close is planned alone" (2026-10-06) — `scaffold`, `scaffold_verdict`, `lattice_reference` from the scaffold, `lattice_lineage`, `_station_reference` probes | `layer_design.py` | the same geometric idea (generate on the full territory, clip at jambs) becomes the GENERAL rule of Z.2 §6; the separate scaffold / verdict / planned-alone / probe machinery goes. "A closed route outranks registration" STANDS: the state carries parity, a forced local event is reported |
| "Generated Wall Systems keep their cross-Z phase at cuts" via a PHASE REFERENCE of the uncut layer (2026-10-08) — `phase_ref`, `_ref_map`, `_chain_path_ref`, `wall_system_reference`, `ws_frames`; and per-span period STRETCHING (`n = round(len / period)`, `P = len / n`) | `wall_systems.generate`, `_wave_paths`, `_chain_path`, `layer_design.build` | period / phase pinned at identified anchors + slack zone (Z.2 §5); the opening jamb is just an anchor |
| Cross-Z TRANSITION of an opening by pre-deformation (`cut_transitions`, `_bend_virtual`, `_virtual_cuts`, `profiles._transition_layers`) | profiles / model / wall_systems | KEPT for now as the only top-down input; later re-expressed as state events {layer born, ramp} (Z.2 §4) |
| Junction corners keyed by FACE PAIR + XY ordinal (`network` corner `key = f'{pair}#{k}'`), per-corner `junction_overrides`, junction constructions re-derived each layer from face-ring tagging | `network.py`, `model._split_wall_systems` / `_apply_wall_systems` | junction RECORD id (M4/M5 registry) as the key; motif kind carried in the state (Z.2 §1, §7); the pass-10 geometry stays as motif implementations |
| "There are no cross-Z hints in the Designer ↔ Assembly contract" | layer_assembly memory → contract | construction state input (Z.2, contract change) |
| Even redistribution of stations inside a segment (`seg_stations`, `counts`) when a count changes | `wall_lattice.plan` | anchored stations + ONE slack cell (Z.2 §5–6) |
| "Transformed-junction lattice — next pass: carry the reference lattice GEOMETRY of each junction territory wall-relatively; track against the previous layer's plan" (2026-10-06 proposal) and Pass B (2026-10-16, reverted: per-layer station carrying / nearest-distance matching, ~350 lines, 15–20 s interactive, lattice still did not stack) | Designer memory | the state chain carries DISCRETE choices by identity, never geometry, never nearest-distance matching |

Pass A's "landings evenly spaced round a ring" (2026-10-16) stays a SINGLE-LAYER preference; through Z one slack cell per segment may break it (invariant 3).

### Z.4 Staged implementation (each stage reviewed and checkpointed on its own)

| Stage | Change | Geometry | Scope / risk |
|---|---|---|---|
| **Z0** | GREEN BASELINE: classify every failing test / golden (A invariant violated · B expected change from approved Pass A / physical width · C encodes superseded behaviour · D unrelated regression); fix / rewrite / rebaseline accordingly; never change production geometry for an obsolete test | none (tests only) | small / low — DONE 2026-10-17, see Z.6 |
| **Z1** | CROSS-Z COHERENCE / SUPPORT HARNESS measuring the PRODUCT requirement (Z.5), independent of the state chain; baselines on representative stacks | none | small / low — DONE 2026-10-17, see Z.5 |
| **Z2** | Anchor identity in production: junction records keyed into the resolved layer, corners keyed by junction id, segment identities on skeleton runs | byte-identical | medium / medium — DONE 2026-10-17 (Z.7; the face-pair key stays the TREATMENT lookup until Z6 / Z8) |
| **Z3** | Topology pass + construction-state chain + state input in the contract (plumbing; state ignored by geometry) | byte-identical | medium / low |
| **Z4** | Skin + Web on the state: pinned stations, slack cell, inheritance on split / merge, lineage scaffold folded in | INTENTIONAL changes on transformed / cut stacks (harness must improve) | large / HIGH — where Pass B died; mitigations: identity not matching, no geometry carried, Z1 as the gate |
| **Z5** | Generated systems on the state: pinned period / phase, slack zone via fade-to-lanes, chain loops likewise, stable Parallel seam; subsumes the phase reference for cuts | intentional | large / medium-high |
| **Z6** | Junction motifs keyed by record: pass-10 constructions reorganised as motifs the record owns; the parallel-lane motif for full-wall ↔ full-wall meetings (the "full-wall junction work") | intentional | large / medium |
| **Z7** | Alignment-independent connectivity: anchors and attachments on the canonical band centre regardless of Inside / Center / Outside (invariant 12) | intentional (Inside / Outside projects) | medium / medium |
| **Z8** | Removal of the superseded mechanisms (§Z.3) | byte-identical | small / low |

M8 (contact overlap) and M9 (junction-preserving transform resolution, §5) are orthogonal and continue; M9 helps Z2 by making junction positions under multiple transform centres well defined.

### Z.5 Measurement — the cross-Z coherence / support harness (Z1, done 2026-10-17)

The gate for every Z stage measures the PRODUCT requirement, never an implementation: `layer_assembly/tests/stack_coherence.py` (metrics; CLI `design_proto/.venv/bin/python -m layer_assembly.tests.stack_coherence [stack …]`) + `test_stack_coherence.py` (baselines in `layer_assembly/tests/coherence/<stack>.json`; a change may only IMPROVE them — `COHERENCE_UPDATE=1` rewrites them after review, `COHERENCE_DUMP=<dir>` writes the per-pair rows). It reads only what a print deposits (the placed printable centrelines of every layer as Generate builds them) and the layers' architectural envelopes, so it needs no change when the pipeline changes. Metrics per adjacent layer pair, in WORLD coordinates:

- **support** — the authoritative bead-level support report (unchanged thresholds): findings of the upper layer by kind (bridge / overlap), total span.
- **correspondence** — for every CONSTRUCTION vertex of the upper layer (roles `lattice`, `wall_system`), the distance to the nearest construction strand of the layer below: max, p95, median. The same for the ARCHITECTURE strands (skins, caps, single beads) as the control. Coherent construction moves a fraction of a bead per layer; a re-phased stitch moves up to half a pitch.
- **topology events** — construction vertices farther than the Maximum Overhang h from every construction strand below (they rest on nothing of their own kind), clustered by bead-width proximity: a cluster = one event (a cell born or dying, a re-phased stretch, a motif that changed).
- **locality / unexplained reorganisation** — each event cluster's distance to the nearest MOVING ARCHITECTURAL BOUNDARY of the pair: envelope points (rings / bare beads, sampled every bead) that moved more than h between the two layers, territory that appeared or vanished, junction points that appeared, ceased or moved more than h. Within LOCAL_REACH (2 × the thickest wall) of such a boundary an event is explained by the architecture; farther it is NONLOCAL — the number that must be 0 when §Z.2 is done.
- **landings** — construction vertices within one bead of an architecture strand of the same layer (the web's landings, lane ends): count per layer and its change between layers, a cheap cell count.

Representative stacks: `scaled_circle_skin_web` (circle × square, Skin + Web, the circle scaled alone over 20 layers), `scaled_circle_interleaved` (same with Interleaved Waves: a generated construction under scale), `reference_network_wave` (three transform groups, the shrinking lens, an opening appearing mid-stack), `real_test3b` and `real_10xtest1` (the operator's projects), opt-in `mixed_benchmark` (`COHERENCE_FULL=1`). Baseline numbers: Z.6.

### Z.6 Baseline established 2026-10-17 (Z0 + Z1)

**Inventory:** the FAST tier had 20 failures (the "20 known failures" of the physical-width checkpoint), the INTEGRATION tier 8 more that the checkpoint had not counted, the strict GOLDEN gate 16 of 23 cases changed. No production geometry was changed. Every failure was classified before any test was touched:

| Class | Failures | What was done |
|---|---|---|
| **B — expected change from approved work** (physical wall width: skins print ½ bead inside the finished faces; Pass A: curved runs are never corners) | route origin ×4 (the authored reference `R` is no longer a printed strand → origins placed on the printed near skin `R.wall_near`); semantic transform ×3 and semantic groups ×1 (connector skins 7 in apart, scaled circle outer skin at 526.5, junction network points on the inset skins 314.5 / ≈ 433.6); source attribution ×1 (the T boundary is the host's printed skin at 103.5); support contract ×1 (bridge depth 10 = 7 + bead); web membership ×1 (a 6 in Skin + Web wall has NO cavity — skins only; fixture now 8 in, plus a new test pinning the no-cavity rule); junction separation sweep ×2 (circle skins r 88.5 / 81.5 — the `[False]` case has a class-A residue, below); the two strict xfails of `test_wall_system_z_coherence.py` now PASS (Pass A fixed that fixture: max drift 0.74 in) → marks removed, kept as passing regressions; golden ×16 (14 physical width + 2 Pass A: bboxes shrink 1.5 in, junction / trim / polyline counts identical except `real_10xtest1`, whose 6 in Skin + Web walls lost their web entirely — zero cavity — print ≈ half as long, and `real_test3`'s +4 branch pieces at its inset host) → re-baselined after review, tags in `golden/cases.py` | expectations updated with the physical reason recorded in each test; goldens re-baselined (23/23 strict) |
| **C — test encoded superseded behaviour** | `test_deferred_print_paths` envelope = full-build regions ×2 + "face beads it prints" ×1: the M0 equality is superseded by the physical-width decision (the architectural envelope stays the finished width; a skin construction's print regions are its inset band) → rewritten as the band relation (every print-ring vertex within √2 · ½ bead (+0.2 for sampled arcs) of the envelope boundary and vice versa, same sources; junction zones within 2.5 T of a junction excluded — there the inset member bands meet in their own corners / fillets / mouths and even the region topology may differ (the lens): Z6 / Z7 territory); `test_route_planning` travel order ×1: asserted "2-opt order ≤ plain nearest neighbour" as a guarantee the planner never gave (it optimises sampled closest-entry distances; the router is unchanged since 2026-10-11) → like-for-like comparison (the router forced into the plain order) over 5 scatters, aggregate must win, no seed worse than 10 % | tests rewritten to the product relation |
| **A — valid invariant violated** (production NOT changed; strict xfails naming the stage) | mixed-system junctions on the reference network ×8 (Skin + Web host × hollow / parallel / interleaved / linked / chain lines, Parallel or Interleaved host × Skin + Web line: a second run or no printed contact between C1 and L1 — invariants 9 / 13) → **Z6**; `test_taper_to_zero_removes_only_that_wall`: the room's web re-phases everywhere when the attached line tapers away (Pass A derives ONE pitch per region from the median cavity; the run set changed: 14.18 → 14.43 in) — invariants 4 / 6 → **Z4**; `test_two_planes…[skin_web]`: layers cut by both planes re-plan their web (the clipped-reference join does not apply: `lattice` report 'planned', ~130 vertices change far from both cuts) — invariant 4 → **Z4**; `test_junction_separation_sweep[False]`: at dx 8 the barely intersecting circle and rectangle (no Line 1) print as 3 runs — their inset skins no longer cross at the grazing junction and nothing joins them (intersecting walls printed disconnected, invariant 13) → **Z6** (a grazing-contact motif) | strict xfails; the suite flags the day they pass |
| **D — unrelated regression** | none found | — |

**Open product questions surfaced (not decided here):** a Route Origin stored on an aligned wall's authored reference strand (older projects) now reports `missing` and falls back — re-attach to the printed near skin, or keep the "never moved onto other geometry" rule? Attribution at a T now gives the branch the landing vertices on the host's printed skin (a diagnostic only). A 6 in Skin + Web wall at a 3 in bead is skins only — intended (zero cavity), but visible in `10xtest1`.

**Baseline numbers (Z1 harness, `layer_assembly/tests/coherence/*.json`, 2026-10-17; "drift" = construction correspondence to the layer below, in; "events" = clusters of construction vertices > h off every construction strand below; "nonlocal" = events farther than 2 T from any moving architectural boundary):**

| stack | layers | support findings | drift max / p95 / median | events (vertices) | nonlocal (vertices) | pairs with nonlocal | landings |
|---|---|---|---|---|---|---|---|
| scaled_circle_skin_web | 20 | 0 | 0.74 / 0.21 / 0.13 | 0 | 0 | 0 / 19 | 290–292, 4 changes |
| scaled_circle_interleaved | 20 | 0 | 1.00 / 0.25 / 0.13 | 0 | 0 | 0 / 19 | — |
| reference_network_wave | 16 | 18 (2 bridge, 16 overlap) | 10.55 / 0.86 / 0.40 | 113 (651) | 77 (381) | 14 / 15 | 1171–1531, 435 changes |
| real_test3b | 73 | 6 (1 bridge, 5 overlap) | 20.0 (cap) / 0.14 / 0.10 | 64 (503) | 39 (57) | 17 / 72 | — |
| real_10xtest1 | 73 | 1 (bridge) | 0 (no construction left: skins only) | 0 | 0 | 0 / 72 | — |

Reading: the two gradually scaled lobe buildings are coherent today (Pass A) — nothing to fix there until a count has to change; the reference network's shrinking lens and appearing opening are the quantified main problem (events on 14 of 15 pairs, half of them nonlocal, 18 findings); `test3b` shows the Interleaved ring re-phasing at its opening / cut layers (nonlocal events on 17 pairs although the architecture never moves). These numbers are what Z4–Z6 must drive to 0 nonlocal events and fewer findings; the test fails on any regression and is re-baselined only after review (`COHERENCE_UPDATE=1`). Runtime: the five stacks ≈ 100 s in one process (`test3b` 72 s); the mixed benchmark is opt-in (`COHERENCE_FULL=1`).

### Z.7 Z2 implementation notes — construction identity (done 2026-10-17; geometry byte-identical)

- **What exists (`design_proto/construction_identity.py`, ~300 lines; wired into `model.PrintLayer._build_effective_rest`, `wall_lattice.plan(identity=…)`, `layer_design.DesignLibrary`):** per resolved layer (every variant: transformed, cut, derived) a report `network['identity']` = `{'anchors', 'domains'}`, carried to the Assembly in `LayerGeometry.diagnostics['identity']`.
  - **Anchors** (architecture, names only): `J<n>` = a persistent junction record present on the layer — the layer's OWN contacts (`junctions.observe_contacts` on the contact-stage geometry, before M6 attachment) matched to the registry's records of its design state by `junctions._match` (members + roles, nearest hints); a junction with no record (walls intersecting only under a transform) is `J?<signature>` — the same name on every layer it is on; `C:<wall>#<k>` = the k-th corner of the wall's AUTHORED path (unrounded vertex turning ≥ 30°, `wall_corners`), an ordinal, never a position; `E:<wall>:start|end` = free ends of an open wall; `O:<opening>:a|b` = opening jambs (Designer and Assembly `asm-…` alike). A junction anchor carries `roles`, `thin` (members without an envelope) and `excursion` (a thin member meeting thick ones).
  - **Domains** = lattice regions: `{'id': job key, 'system': owning Wall System (or the legacy infill id), 'walls': the THICK bounding walls (source boundaries irrelevant — invariant 7), 'thin': thin walls whose lanes reach the region (never members — invariant 8), 'segments', 'runs', 'unresolved'}`. Each skeleton run of the planner is labelled by the anchor at each end (skeleton junction → the junction anchor within 2 t; dead end → the jamb / wall end within 1.5 t, else the junction that cut it) and at each detected corner (wall corner or junction kink within 2 t); **segments** = the stretches between consecutive anchors, `id = unordered anchor pair`, disambiguated only when a region repeats one (`a>b` cyclic order round a loop — forward = outer ring on the left; `~wall` for the two runs of a lens). A corner-less loop is `loop:<wall>`. Anything the architecture does not explain within reach is `?…` and counted in `unresolved` (0 on every test stack), never invented.
  - **Lineage-scaffold / reference-clipped layers** (superseded mechanisms, still live): the member prints the scaffold / the uncut reference clipped, so its domain's segments are the reference's own (`PrintLayer.lattice_identity`, from the scaffold's plan report or the uncut build's identity) and `inherited: True`. Goes with Z4.
  - **Production junction corners** (`network['junctions'][k]`) gain `junction` = the record id (nearest junction anchor whose members include the corner's walls, within reach — `junction_of`, the `map_overrides` rule). The TREATMENT lookup still uses the legacy face-pair key (removal: Z6 / Z8).
  - **Events:** `construction_identity.diff(previous, current)` → per kind (junction / jamb / end / corner / segment / domain) the ids born and ceased — junction appearance and disappearance are explicit here (the Z3 chain's input), nowhere else.
  - **Registry plumbing:** `DesignLibrary(…, junctions=registry)` — given the project's registry, or built on demand from the designs (`registry()`, stable ids across edits: rebuilt with the previous one); `junction_records(design_id)` = the records active in that state; `_layer(doc, design_id)` carries them into every build (build, verdict, cut join, scaffold, envelope). Worker processes receive it in the library JSON. The Assembly requests (`/api/assembly/resolve|geometry|print_plan`) accept an optional `junctions` registry (the client does not send it yet: server-built ids are deterministic and identical across the stack; they can differ in numbering from a stored registry after retirements — a cosmetic gap until the client passes `project.junctions`).
- **Measured:** lobe building (circle × square, Skin + Web; circle scaled 1.0 → 0.7 and through the 20-layer Assembly stack): anchors `C:R#0–3`, `J1`, `J2`; one domain {C, R}; 5 segments; identical ids on every layer, `diff` empty everywhere; scaled to 0.25 (circle inside the square): J1 / J2 CEASE, corners unchanged. Reference network (16 layers, three groups, Line 1 a connector, lens, 'gaps' opening): all six junctions named by their records on every layer; events = jambs `O:rect1_top:a|b` born at layer 8 (design switch), `J4` / `J5` (the lens) ceased at layer 11, segment events only at 8 and 11; corners / ends never change. `10xtest1`: three junctions, the thin Out-and-Back is `thin` / `excursion` on its junctions and in no domain; cuts above layer 48 cease the junctions. THIN into a Skin + Web host: the host's domain is `walls = [W]`, `thin = [L]`; the lanes' mouth is a local feature of the web named by the junction record (today's skeleton splits there — a Z4 / Z6 geometry question, named now so it can be treated locally).
- **Cost:** 1–3 ms per full build (contacts + corners + lookups), ≈ 3 ms per envelope build; registry 4 ms (10xtest1) once per library.
- **Gate:** strict golden 23/23 after re-baselining FULL hashes only (the `identity` / `junction` keys are new label fields: `golden/harness.LABEL_KEYS`; `geom` hashes, PrintPlan and `.mudjob` identical before the re-baseline); Z1 harness metrics EXACTLY equal to the Z1 baselines on all five stacks; tests `design_proto/tests/test_construction_identity.py` (15) + `layer_assembly/tests/test_identity_stack.py` (3, integration tier).
- **Not done here (by design):** no construction state, no change to how anything is planned; generated Wall Systems (Interleaved / Linked / Chained Loop / Parallel) get the domain + anchors but no segment ids yet (Z5); the override lookup by record id (Z6 / Z8); the client sending `project.junctions`.

## §F. FINAL PRODUCT DECISIONS (2026-10-15, supersede earlier text)

1. **TOPOLOGY MAY CHANGE THROUGH Z.**
   - Persistent wall identity does NOT imply persistent junction topology.
   - When Assembly transforms make walls stop intersecting, their junction ceases to exist on that layer; above it they print as independent objects.
   - Where transformed geometry begins to intersect, a junction appears.
   - This is valid architecture, never an error. Walls are never extended / stretched to preserve a junction.
   - Existing support / maximum-overhang rules decide whether the layer-to-layer change is printable.
   - Replaces §5's "JUNCTION LOST → not generated" and Q1.
2. **NO CONTINUATION-DISTANCE LIMIT.** While transformed walls intersect, the junction resolves from the transformed architecture, whatever the distance moved. Gradual change is governed by support / overhang. Replaces Q2 and §5's sanity bound.
3. **OUT-AND-BACK HOST STRAND IS RESOLVED GEOMETRICALLY, PER LAYER.**
   - No host wall / strand identity is persisted from history.
   - At each resolved layer the branch is followed to where it reaches the exterior of the wall construction; the printable strand it actually meets there becomes the host.
   - That SAME strand diverts out, does the out-and-back, returns and resumes its own path.
   - Host selection happens at the START of the excursion. On return, incidental crossings with other strands do NOT change the host: Wave B stays unchanged and the excursion rejoins Wave A. Geometric crossing ≠ topological connection.
   - Replaces §6's "identity first" host selection and Q3. §6's fencing via `join_group` / `join_points` stands.
4. **MIGRATION DROPS OLD VARIANT-SPECIFIC PHYSICAL PROPERTIES.** They are reported, not preserved, including `10xtest1` Variant 1's round junction r 7. Resolves Q4.

Junction records (§4) therefore describe RELATIONSHIPS of walls in the authored architecture. Per resolved layer, a junction is present or absent according to the actual intersection of that layer's transformed architecture.
- Evidence comes from code tracing at checkpoint `d89dd8e`, backend probes, reproductions on the user's saved projects (`Documents/mud print models/*.muddesign`), and three read-only code audits.
- It incorporates the user's product decisions of 2026-10-15:
  - remove Wall Groups;
  - Base / Variants are registered layout STATES;
  - no Wall Types;
  - construction independent of states;
  - material project-level;
  - junction-preserving transforms;
  - the continuous-excursion rule;
  - two overlap concepts.

**Workflow (unchanged, must stay):**
- Designer → Assembly → Wall Systems → Print Simulation.
- Resolution order: Designer architecture → Assembly composition (states, transforms, cuts, profiles, headers) on LIGHTWEIGHT architectural geometry (envelopes) → Wall Systems generate construction from the assembled architecture → PrintPlan / Print Simulation.
- Wall Systems stay deferred after Assembly.

**Conflict with a documented decision (acknowledged).**
- The 2026-10-14 baseline cleanup moved the "Wall Geometry" panel (junction / end-cap treatment) into the Wall Systems workspace ("Wall Geometry belongs to Wall Systems").
- This proposal treats junction / corner / cap SHAPE as architectural, edited in the Designer.
- Only printable MOTIFS remain construction.
- Reason for the change: these settings change the envelope (faces, material rings), not only lanes — `network.round_junctions`, `fillet_ring`, `model.py:4771-4789`, caps `model.py:3037-3075`.

---

## 0. Diagnosis recap (verified; unchanged)

1. **Wall properties are stored per Layer Design document:**
   - Wall Geometry is written to the ACTIVE design (`setWallGeometry`, `app.js:6558-6580`).
   - `wall_groups` / `junction_overrides` / `source_paths` fields are Variant-patchable (`derive_delta`, `layer_design.py:225-262`).
   - Two thickness editors write different scopes.
   - → Symptoms A, B, part of C.
2. **No persistent junction.** Trims, junction overrides and attachments are keyed to contacts re-derived on every build:
   - `trim.Section.matches` (`trim.py:48-52`);
   - face-pair + XY ordinal (`network.py:1089-1140`);
   - face contacts;
   - independent source transforms with connector / hub rules (`semantic_transform.py:298-338`).
   - → Symptoms D, E, F, G(3).
3. **H:**
   - `contact_overlap` reaches only Skin + Web landings. `wall_systems.generate(…, contact)` receives it and never uses it.
   - Other contact depths are constants.
4. **G(1) and G(2) were isolated editor bugs, both fixed:**
   - G(1): trim-aware snapping (Pass 1).
   - G(2): exact ghost clipping (`d89dd8e`).

---

## 1. Final ownership model

| Owner | Owns | Never owns |
|---|---|---|
| **WALL** (a source path; identity = source id) | Source geometry (its reference path); **thickness** (thick walls) or the **THIN state** (§1.1); **alignment / reference** (Centered default); print reference; **Corner R** (source corners); **cap shape** of its free ends (flat / rounded corners / full round + R); its **Wall System assignment** | Anything per state; anything per layer |
| **JUNCTION** (persistent relationship of ≥ 2 walls; §4) | Its members (walls + how each meets it); **architectural junction shape** (miter / round + R, or inherit the project default) | An XY position (resolved, never stored as identity); printable motifs |
| **LAYOUT STATE** (Base / Variant) | Which walls and junctions EXIST in the state (Variant may add / remove); **openings** (present / absent / position / width); trims on its own walls / junctions; route origins of its geometry; region overrides | Thickness, alignment, Corner R, caps, junction shape, construction, material, the geometry of walls it inherits |
| **ASSEMBLY** | Which state per section; sections; transforms + transform groups (membership = wall ids); profiles (openings, planar top cuts); headers; support overrides | Wall properties; construction |
| **WALL SYSTEM** (project) | HOW a wall prints: type + params + web; printable corner / junction / end MOTIFS; excursion routing and lane hand-offs (§6); an optional, justified per-system contact override | Placement; thickness; junction shape; any per-state difference |
| **MATERIAL** (project) | `bead_width`, `contact_overlap`, `return_overlap`, `physical` | Per-state or per-design values (a future height-dependent process change would be an explicit Assembly / process concept) |
| **PROJECT DEFAULTS** | Default junction shape (miter / round + R) | — |

### 1.1 Thin walls (out-and-back / single bead) — not a free thickness

- **Code fact.** A path with no thickness is a SINGLE-BEAD wall.
  - An open one with physical rules prints two passes (lanes W − R apart, U-turn): `model.py:4474-4481`, `material.return_separation()`.
  - Its physical width is `W` (single pass) or `2W − R` (two-pass), derived from material, never a free value.
  - `wall_systems` type `single` also means "no envelope" (`_effectiveWall`, `app.js:3420`).
- **Final model:** a wall is either:
  - **THICK** — `thickness` > 0, a free architectural value, with alignment;
  - **THIN** — no free thickness. Its realised width is shown read-only, e.g. "Thin · 5.25 in", from bead width and return overlap.
- **Construction of a thin wall:**
  - Single pass for closed thin walls.
  - Out-and-Back for open ones.
  - `return_overlap` changes the realised width within material bounds (`R ∈ [0, W − 0.25]`).
- **THIN → THICK (2026-10-15 correction):** an explicit thickness is never below the THIN width. `+` from THIN gives the first 0.5 in step at or above it (5.25 → 5.5), and `−` from that minimum returns to THIN.
- **Persistence:** THIN is today's "no envelope / thickness 0". No new storage, just an explicit state in UI and docs.

---

## 2. Wall Group verdict — REMOVE completely

Final audit of every responsibility. The full table from revision 2 still holds; the final classification:

- **Thickness, alignment, print reference, thick vs thin** → the WALL.
  - The per-wall store already exists and is consumed end to end for ungrouped walls: `path.envelope`, `wall_groups._own_walls` `:152-172`, `_effectiveWall` 'own', `setWallThickness`.
- **Wall System by group** → per-wall `members`, which already exist. Real projects use only `members`; no saved project assigns by group.
- **Planar-cut "Wall Groups (all members)"** → multi-select / select-network. Saved planes in real projects already use `sources`.
- **Build-record composition** → internal, keyed by (system, thickness, alignment, reference). Groups are not needed:
  - Material regions come from contact.
  - Construction continuity comes from equal keys (`model.py:3425-3530`).
- **Record id = group id** (Skin + Web web `owner`, migrated planes) → deterministic record ids + migration remap.
- **Envelope-only builds** → per wall / envelope key.
- **Junctions, transform groups, openings, profiles, PrintPlan, source attribution** → never read groups.
- **Group name, Variant group patches, undo snapshots, legacy split** → scaffolding / legacy.

**Residual capabilities to replace (none essential):**
1. Editing several walls at once → **multi-select batch edit** in the Paths list and the Wall Systems wall list, plus a "select connected network" helper (⛓ network badges already exist).
2. References that grow with a group → explicit wall lists (deliberate).
3. Linked thickness → **dropped by decision** (no Wall Types).

**Nothing irreducible is lost.**

---

## 3. Variant identity model

**Code facts:**
- A Variant = parent document + delta. Inherited records keep their ids (`apply_derivation`, `layer_design.py:198-224`).
- Additions are new records with globally unique ids (one `_idCounter`, `app.js:6432`).
- Removals are `None` patches.
- So **wall identity across states is the source id**, and it already works.
- **Real projects use Variants exactly as intended:** their deltas hold only `openings` (and older ones `route_origins`). The only divergence anywhere is `10xtest1` Variant 1 `settings {junction_style: round, junction_radius: 7}`.

**Rules:**
- **Wall A in several states is one wall.** Its properties live on the record in the design that INTRODUCED it, its lineage owner (Base for Base walls).
- **A Variant patch may only:**
  - remove an inherited wall;
  - change its OPENINGS / trims / route origins / region overrides;
  - add new walls (owned by that Variant, with their own properties).
- **Enforcement uses the existing lineage mechanism.** It already strips descendant overrides of the LATTICE DEFINITION (`_without_lattice_overrides`, `layer_design.py:265-290`). Extend it to a WALL DEFINITION:
  - geometry fields;
  - `envelope`;
  - `corner_radius`;
  - `cap`;
  - Wall System assignment (project-level anyway).
- **Editors write the owner regardless of the active design**, as `setWallThickness` already does through `_catalogDocs`.
- **Resolution of a wall's properties in any state** = its owner record. State never participates.
- **Walls only some states have** (a bench in the lower sections, a buttress below, Base = the simpler upper state) are ordinary walls owned by the Variant that adds them. Their junctions with inherited walls are owned by that Variant too (§4).

---

## 4. Persistent junction data model

```text
document.junctions[]   (keyed collection; lineage-owned like walls)
  {
    id:       "J7"                        — stable, generated once
    members:  [ { wall: "p1", at: "through", s: 0.25 },     — passes through J (closed form,
                                                             continuing line): s = arc fraction
                                                             where the junction lies (a HINT
                                                             for continuity, not identity)
                { wall: "p5", at: "start" | "end" } ],      — ends at J
    shape:    "inherit" | { style: "miter" | "round", radius },   — architectural envelope shape
    kind:     "cross" | "tee" | "corner" | "hub"            — derived from member roles (info)
  }
trims[]  →  { id, source, between: [J_a, J_b], side: {wall: inside?} }
           (a section of `source` between two junctions on it, or a junction and an open end)
```

- **Identity** = the set of member walls + each member's role. It is never a face pair and never an XY ordinal.
  - Two walls meeting twice (circle × rectangle) have two junctions, J1 and J2, told apart by their arc HINTS. The hints are refreshed after every successful resolution.
- **Detection:**
  - A new contact found by the Designer, with the same geometry as today (`network.py`, `trim.contacts`), creates a junction.
  - A snapped / drawn end creates its junction explicitly.
  - Existing junctions are re-matched to contacts by member set + nearest hint. They are never re-keyed silently.
  - A junction whose contact disappears in the authored Designer geometry is removed only by a user edit (delete / move away). It is reported, not resurrected.
- **States:**
  - A junction exists in a state iff all its members do.
  - If a Variant removes one member of a 3-wall junction, it becomes a 2-wall junction there. If two of the three are removed, it is inactive.
  - Trims bounded by an inactive junction are inactive in that state.
- **Mixed thickness / alignment / construction:**
  - Members are SOURCE walls. Each member's envelope is its own (thickness, alignment).
  - The junction's architectural envelope is the union of the member bands, mitred or filleted by `shape`.
  - Nothing requires equal thickness or a shared system.
- **E fixed by construction of the model:**
  - A branch joins the WALL (member `at: start/end`, meeting the through-wall's SOURCE line).
  - Which physical face it touches is derived: the face of the through-wall on the branch's side.
  - Inside / outside alignment can no longer redirect it to the exterior face.

---

## 5. Transform resolution (Assembly)

**Principle.**
- Transform SOURCE architecture. Each wall gets its layer's / group's uniform scale + translation (no rotation exists).
- Then RE-RESOLVE every junction from the transformed sources.
- Junctions are relationships; their XY is an output.
- Construction (beads) is generated afterwards, as today. Nothing printable is stretched.

**Algorithm per layer, per junction J (members already transformed):**

1. **All members share one transform** → J′ = T(J). Exact, identical to today.
2. **Through-members** (closed forms, lines continuing through J) keep their transformed curves; they never extend.
   - With ≥ 2 through-members, J′ = the intersection of their transformed curves whose arc positions are nearest the hints (continuity tracking).
   - **Example — circle × rectangle, circle scaled alone:**
     - The rectangle's edges stay put; the scaled circle meets them at new points J1′, J2′.
     - The visible rectangle = the rectangle minus its trimmed section between J1′ and J2′.
     - So its edges appear to continue / stop along their own direction — exactly the requested behaviour, with no new mechanism for closed forms.
3. **End members** (a wall ending at J):
   - The end slides along its OWN source direction. A uniform scale + translate preserves directions, so the carrier line of a straight end is well defined.
   - It slides to the intersection of that carrier with the resolved through-member(s) / partner, nearest the old end.
   - Lengthening a curved end continues along its end tangent; shortening trims along the curve.
   - **Replaces today's connector rule**, which re-fits an attached open wall by a SIMILARITY fixing both end images (`semantic_transform.py:330-338`) and therefore rotates and scales it.
4. **Two end members only** (an L / end-to-end hub, possibly in different groups) → the intersection of their two carriers.
5. **Both ends of one open wall attached to differently-transformed hosts** → each end slides independently along the same carrier (its direction is invariant). No rotation.

**Ambiguous / impossible cases → explicit, per-layer report. Never a guess:**

| Case | Behaviour |
|---|---|
| Through-members no longer intersect (circle shrinks inside the rectangle, forms separate) | **NO JUNCTION on that layer** — valid topology (§F1); the walls print independently; support / overhang rules judge the transition |
| Carrier misses the host (end would have to extend to infinity / past the host) | NO JUNCTION on that layer (the end stays where its own transform puts it) (§F1) |
| Carrier nearly parallel / tangent to the host (grazing, angle < ~5°) | AMBIGUOUS → reported, not generated |
| Several intersections on the carrier | The one nearest the old end along the carrier. If it is not also nearest the hint on the host, AMBIGUOUS |
| The resolved point leaves the host member's segment / edge (passes a rectangle corner onto the next edge) | Allowed on closed forms (arc continuity). Reported as "junction moved past a corner" |
| A 3+ wall junction whose through-members' intersection splits (they no longer meet at one point) | **JUNCTION SPLIT** reported. End members resolve against the host their record names (`members[].wall` they meet); if that is ambiguous, not generated |
| Long extension / shortening | No limit (§F2); reported as information only |
| Line × line, line × circle, circle × rectangle, curves | Covered by the rules above (closed-form intersection: sampled + refined; Bézier: the same with parameter tracking) |

- **Multiple transform centres:** each wall takes its own group's transform. Each junction resolves by the rules above.
  - Today's hidden behaviours disappear: hubs following the smallest id, insets ignoring membership, unassigned connectors.
  - Exceptions are explicit:
    - an InsetPath still derives from its parent (it is not a wall);
    - a WallRelation dependent derives from its driver (reported if their groups differ).
- **The whole network is never moved because one member moved.**

---

## 6. Construction junction model — continuous excursion

**Code facts:**
- Today's mixed-system junction already SPLICES an out-and-back branch into ONE host path (`wall_systems.connect_branch`, `:1836`) *(diagnosis-time code: `connect_branch` was no longer reached by the pipeline and was deleted in the 2026-10-16 cleanup; M7's `splice_excursions` kept its host search as `_resolve_host`)*:
  - both lanes run straight to the host's real generated geometry;
  - the pair of hits must lie on one host path, close along it;
  - the short host stretch between them is cut out — "branch and host become one circuit".
  - The geometry is already an excursion.
- What is INFERRED, not explicit:
  1. **Which host strand:** "the first pair of ray hits on one path". It could pick a web strand or a wrong lane where those are hit first.
  2. **What is connected in the print route.** The router merges nodes wherever segments CROSS (`toolpath_proto/graph.py` header: "two segments crossing" is a real contact). An excursion crossing Wave B therefore becomes a routing junction with Wave B.
- The router ALREADY has the exact tool needed (`toolpath_proto/geometry.py:47-55`):
  - `Strand.join_group` + `join_points`: contact between different join groups is a routing junction ONLY at the listed join points;
  - elsewhere the beads touch and the route passes straight over.
  - Used today for solid-infill webs.

**Rule:**
1. **Strand identity.** Every construction emits strands with stable ids and a role: `<wall>/<system part>/<k>` (lane k, wave k, chain run, skin inner / outer, web). Only EXPOSED strands are excursion hosts: the outermost lane / skin / wave facing the branch side.
2. **Host selection (identity first, geometry second).**
   - The junction's member roles say which wall is the host and which side the branch is on.
   - Candidate host strands = that wall's exposed strands on that side.
   - Among them, the first one hit along the branch lanes (today's ray test, restricted to candidates).
   - Both lanes must splice into the SAME host strand. If not → reported, not guessed.
3. **Splice.**
   - The host strand is cut at the two splice points p1, p2 (the short stretch between them removed, as today).
   - The EXCURSION (lane, U-turn, lane) is emitted as its own strand with `join_group = "exc:<J>"` and `join_points = [p1, p2]`.
   - Effects:
     - the router can enter / leave the excursion only at p1 / p2, so the host is forced to be printed as host → excursion → same host, continuing its own trajectory;
     - an incidental crossing of the excursion with Wave B, or with any other strand, is a pass-over: Wave B is unchanged and never switches;
     - the host strand's own deliberate crossings (Interleaved waves crossing each other) keep today's semantics. Only the excursion is fenced.
4. **Per construction:**
   - Straight / curved skin → the skin strand.
   - Interleaved / Linked → the wave strand first intercepted on the branch side.
   - Chained Loop → that chain run (the short stretch on the hit lobe).
   - Parallel → the exposed lane.
   - Skin + Web → the skin on the branch side (webs are never hosts).
   - Never a global modification: other strands are untouched.
5. **Representation for later stages.** The junction's construction realisation is recorded in the build report as `{junction, host strand id, p1, p2, excursion strand id}`. PrintPlan / Simulation consume strands. Downstream code never infers a connection from geometry.

---

## 7. Corners and caps

| Concept | Owner | Edited in |
|---|---|---|
| Corner of one wall (rectangle corner, polyline vertex): Corner R | WALL (source) | Designer path properties (as today) |
| Free-end cap SHAPE (flat / rounded corners / full round + R) | WALL | Designer path properties (moves from Wall Systems "Wall Geometry") |
| Junction SHAPE (miter / round + R) | JUNCTION (default: project) | Designer: select a junction ◆ (markers already drawn); project default in a small Designer setting (moves from Wall Systems) |
| Printable corner / junction / end MOTIFS (mitre lanes, wave fades, chain eyes, end lanes, U-turns, staples) | WALL SYSTEM | Wall Systems (internal today; exposed only where a real choice exists) |
| Construction-imposed shape (an out-and-back end is a U-turn: `full_round`, `model.py:2691-2697`) | Construction REQUIRES it; the architecture is not silently overwritten | Shown as "set by construction" on the thin wall's cap |

---

## 8. Overlap — final

**Ownership:**
- `contact_overlap` (O) — project MATERIAL default. The centrelines of intentionally side-contacting beads are W − O apart. "Kiss" = 0.
- `return_overlap` (R) — project MATERIAL; ONLY the out-and-back / thin-wall return lane (width 2W − R).
- An optional per-Wall-System override of O only with a stated construction reason (candidate: Linked interlock depth). No third concept.

**Hard-coded contact behaviour to migrate to O (audit; intentional geometry changes, milestone M8):**

| Where | Today | Proposal |
|---|---|---|
| `wall_systems.generate(…, contact)` | plumbed, unused | Use it |
| Linked closest approach (`wall_systems.py:577-579, 785`) | 0 (centrelines touch) | ≥ W − O |
| Parallel lane spacing 2·H0/(N−1) (`:574, :781`) | Thickness / N only | Validate against W − O (report or choose N) |
| Contact staple spacings (bead, 1.5b, 0.5T, 0.5b) + touching tolerance 0.25·bead (`model.py:2083-2096, 3625`) | Constants | W − O where they mean side contact |
| Push reach 0.5·hT + 0.25·bead (`model.py:3923-3933`) | Constant | Review: side contact → O |
| Skin + Web landings (`wall_lattice.py`) | Already W − O | Keep |

**Not contact (stay as is):**
- face fits (½ bead inside faces, mitre clearance 0.5·bead − 0.05, cap insets);
- excursion splices (path CONTINUITY, not side contact).

---

## 9. Migration (`.muddesign` schema 7, one step `_v6_to_v7`)

1. **Wall Groups → walls.**
   - Each group's `thickness` / `align` / `print_reference` → `envelope` on every member, on the member's owning design.
   - Ungrouped walls keep their `envelope`, or stay THIN.
   - `wall_groups` removed.
2. **Variant wall additions / removals** are kept as they are (already records / None patches).
3. **Variant property divergences** (any patched WALL DEFINITION field, group field, Wall Geometry `settings`, `junction_overrides` patch, `material` setting):
   - removed;
   - Base / owner value wins;
   - every one listed in a **migration report** shown on Open.
   - **Real projects: exactly one** — `10xtest1` Variant 1 round junction r = 7. This is an intentional visible change, to be confirmed with the user.
4. **Wall Systems:**
   - `groups` → `members` (expanded per group, union across designs);
   - `designs` scope removed. No saved project uses it; a scoped project gets the report and the first system wins;
   - build-record ids made deterministic; Skin + Web owned infills' `owner` remapped.
5. **Material:** Base / root `material` → project material. Variant overrides reported and dropped (none in real projects).
6. **Planes:** `systems` (group ids) → `sources`.
7. **Junction shape:**
   - Base `junction_style` / `junction_radius` → project default.
   - Base `cap_style` / `cap_corner_radius` → each wall's `cap`.
   - Existing `junction_overrides` (none in real projects) → mapped to the junction records detected at their location; unmappable → report.
8. **Junctions + trims** (none of this changes geometry):
   - Junction records are created by detection on each design's authored geometry.
   - Trims are converted from bounding-source sets to junction references by resolving them on the authored geometry (the existing `_pin_trims` machinery does exactly this per build), so every trim that resolves today removes the identical section.
   - Trims that are `unresolved` today stay unresolved and are reported.
   - All real projects carry 2–5 trims.

**Compatibility.**
- Without divergences, the printed geometry is identical (golden tests, §11).
- Older readers (schema 1–6 migrations, legacy `wall_systems` documents, `path.wall`, `network_walls`) stay as one-way migration code. Since the 2026-10-16 cleanup `walls.migrate_project` is the ONLY reader of those forms (and drops pairwise `lattice_instances` with a report): the model, the Designer page and the Assembly read per-wall `envelope`s and project Wall Systems only.

---

## 10. Implementation plan (each milestone small, independently testable)

| Milestone | Change | Printed geometry |
|---|---|---|
| **M0** | Golden snapshot harness + frozen snapshots (§11) | — |
| **M1** | Lineage ownership of WALL DEFINITION + material; all wall-property editors write the owner (no schema change; existing Variant overrides still honoured, flagged) | **Byte-identical** |
| **M2** | Schema 7 ownership migration: Wall Groups → wall `envelope`, Wall System `groups` → `members`, `designs` scope removed, planes → `sources`, material → project, Wall Geometry → project default + wall caps, divergences dropped + reported | **Byte-identical** except reported divergences (`10xtest1` Variant 1 junction r7 → Base) |
| **M3** | Designer / Wall Systems UI: thickness on collapsed Path rows, THIN state shown, Wall Groups UI removed, multi-select batch edit, per-wall Wall System assignment, cap / Corner R in path properties, junction shape on ◆ selection + project default; Wall Systems keeps construction only | **Byte-identical** |
| **M4** | Persistent junction records in SHADOW mode: detected exactly as today, persisted, overrides mapped; geometry still from contacts; a consistency report compares them | **Byte-identical** |
| **M5** | ⟵ **FIRST INTENTIONAL JUNCTION-GEOMETRY CHANGE.** Trims reference junctions; a branch joining an existing junction becomes a member (symptom D); a trim bounded by a deleted wall is reported (G3) | Identical wherever today's trims resolve; changes only the D / G3 cases (strict xfail flips) |
| **M6** | Source-line attachment: branches meet the WALL; construction picks the face (symptom E) | Changes branch / face contacts under inside / outside alignment; Centered cases mostly identical |
| **M7** | Construction excursions: strand identity, host selection by identity, excursion strands with `join_group` / `join_points` | Geometry ≈ identical (same splices where the first hit was already right); ROUTE ORDER changes at excursions |
| **M8** | `contact_overlap` wired into Wall Systems (+ optional justified override) | Intentional lane-spacing changes (Linked, staples) |
| **M9** | Junction-preserving transform resolution (§5) replacing connectors / hubs; explicit LOST / AMBIGUOUS / SPLIT reports | Intentional for multiple-centre cases; whole-footprint transforms identical |
| **M10** | Remove `wall_groups.py` composition paths and legacy writers (readers stay for migration) | Byte-identical |

M1–M4 deliver the visible ownership simplification with no geometry change. M5 onward changes junction geometry deliberately, one symptom per milestone, each with its golden update reviewed.

---

## 11. Golden test plan (freeze BEFORE M1)

**Snapshot content per case.** Per design and per Assembly layer:
- printable centrelines + material regions (rounded 1e-6);
- the Assembly resolve report (instances, cut specs, profile reports);
- support findings;
- the PrintPlan stream;
- the `.mudjob` with fixed `name` / `generated_at` (byte-identical text).

Mechanism: `GOLDEN_UPDATE=1` regenerates. Each milestone's diff is reviewed. A case expected to change at milestone X is tagged with X.

**Real projects** (copied into `tests/golden/projects/`):

| Project | Exercises |
|---|---|
| `test 3.muddesign`, `test 3 (1).muddesign` | Schema 4; circle + rectangle, trimmed overlap; Interleaved; Variant openings; profiles |
| `10xtest1.muddesign` | Schema 6; Skin + Web group of 6 in × thin out-and-back line (real mixed thickness / construction); Variant round-junction divergence (tag M2); two planar top cuts; header; Assembly inward scale |
| `test save.muddesign`, `test save 2.muddesign` | Schema 1 legacy migration path; route origins in a Variant |
| `contracts/mudjob/fixtures/designer-export-*.mudjob` | Already regression-tested — keep |

**Synthetic cases** (documents in `tests/golden/cases/`):

| # | Case | Tagged to change at |
|---|---|---|
| 1 | Circle + rectangle, trimmed overlap (Hollow, Interleaved, Chained Loop) | — |
| 2 | 1 + a branch FROM an existing junction (single bead and thick) | M5 |
| 3 | 1 + a branch crossing a trimmed section | M5 |
| 4 | Mixed thickness: 10 in rectangle (Centered / Inside) × 3 in thick branch × thin out-and-back branch | M6 (inside cases) |
| 5 | Mixed Wall Systems: Chained Loop ring × Parallel line; Skin + Web × Hollow | M7 |
| 6 | Interleaved waves + out-and-back excursion whose return crosses another wave | M7 (route order) |
| 7 | Chained Loop + excursion | M7 |
| 8 | Multiple-centre transform: circle in group A scaled, rectangle in group B; a T-attached line; an L hub across groups | M9 |
| 9 | Whole-footprint transform of case 1 | — (must stay identical) |
| 10 | Openings + planar top cut (Linear / Stepped / Curved) + self-supporting opening on case 1 | — |
| 11 | Variant adding a bench wall in lower sections + Variant removing a buttress | — (M4: junction records) |
| 12 | Linked / Parallel lane contact | M8 |
| 13 | PrintPlan + `.mudjob` fidelity: every case exported, G-code compared byte for byte | per case tag |

---

## 12. Remaining product questions

Resolved by §F (Q1 junction loss = valid topology; Q2 no limit; Q3 host strand
resolved geometrically per layer; Q4 drop + report). Anything new found during
M0–M4 is listed in the M0–M4 implementation notes below.

---

## M0–M4 implementation notes (2026-10-15)

### M0 — golden regression foundation (done)

- **Harness:** `layer_assembly/tests/golden/harness.py`; test `layer_assembly/tests/test_golden.py`.
- **Cases:** 22 `.muddesign` inputs in `golden/projects/`:
  - 5 real projects;
  - 17 synthetic, from `golden/cases.py --write`, frozen as schema-6 files.
- **Every run** loads each file through `project_file.load` (all migrations), then the browser's requests:
  - `/api/effective_paths`: full + envelope, per Layer Design;
  - `/api/assembly/resolve`;
  - `/api/assembly/print_plan` (detail 'full');
  - `/api/assembly/print_job` (`generated_at` stripped).
- **Snapshot** = SHA-256 of canonical JSON (floats 1e-6) + readable summary.
- **Updates:** `GOLDEN_UPDATE=1` only. `GOLDEN_DUMP=<dir>` for diffs.
- **Determinism:** identical hashes in-process and across processes.
- **Runtime:** ~2 min.
- **Expected-change tags:** `cases.CHANGES_AT`.
- **Baseline facts frozen:**
  - `real_10xtest1` already has 3 UNRESOLVED trims (p9, p10, p11 — bounded by its out-and-back line), the deferred trim topology in a real project;
  - `syn_branch_at_junction` / `syn_branch_across_trim`: both overlap trims unresolved (M5 targets).

### M1 — persistent wall-property / material ownership (done)

- **Client** (`app.js`, "PERSISTENT WALL-PROPERTY OWNERSHIP"):
  - While a Variant is open, every edit of an OWNED field made since the last sync is written THROUGH to its owner when the design syncs. The Variant's delta never gains it.
  - Owner = the lineage root for document-level fields; for a record, the design that introduced it.
  - Owned fields:
    - document: `junction_style`, `junction_radius`, `cap_style`, `cap_corner_radius`, `corner_radius`, `material`, `junction_overrides`;
    - per wall: `envelope`, `wall`, `corner_radius`;
    - per Wall Group: `name`, `thickness`, `align`, `print_reference`.
  - Not owned (layout state, stays the Variant's): wall geometry, added / removed walls, openings, trims, route origins, region overrides.
  - "Since the last sync" (`_variantSynced`) keeps EXISTING divergences untouched.
- **Server:** `layer_design.divergences` / `describe_divergences` report stored divergences. `project_file.load` adds them as warnings; the Designer status names them when the Variant is opened.
- **Tests:**
  - `layer_assembly/tests/test_ownership_ui.py`: real `10xtest1`, real backend. 14 checks; verified to FAIL without the write-through.
  - Divergence report unit test.
- **Gate:** full suite 2489 passed + 1 xfail, one test updated for the new report (`test_project_file`: divergence warnings expected); toolpath 92; golden byte-identical (full hashes equal M0).
- **Harness refinement:** components now also carry a label-stripped `geom` hash (asserted). The full hash is asserted with `GOLDEN_STRICT=1`.
  - Reason: the build-record id (e.g. `WG1`) appears only in labels. Renaming it leaves the G-code byte-identical (measured on `10xtest1`).

### M2 — schema 7: Wall Groups removed (done)

- **`design_proto/walls.py`** (new) is the per-wall architecture:
  - `compose` builds the same legacy build records per wall (record = Wall System + envelope; an unassigned thick wall → `wall:<pid>` Hollow; a thin wall → none);
  - `migrate_project` / `migrate_parts` do the one-way migration.
- **`wall_groups.py`** is reduced to the LEGACY READER (`split_document` / `split_designs` for `_v1_to_v2`).
- **`project_file`:**
  - `SCHEMA_VERSION = 7` (`_v6_to_v7` is a no-op step);
  - `validate` runs `migrate_project` (idempotent) on EVERY open / save, so grouped data from anywhere is converted; each change is a warning.
- **Migration rules:**
  - Base group → each member's own `envelope` (thickness, align, print_reference);
  - Wall Systems: `groups` → `members`; Variant scope (`designs`) removed (reported);
  - a Variant-added wall in a group gets the group's (Base) envelope;
  - every other Variant-specific physical property is DROPPED + reported:
    - group overrides;
    - removed members;
    - Variant settings in `OWNED_DOC_SETTINGS`;
    - `junction_overrides`;
    - legacy per-state `wall_systems` / `network_walls` patches;
    - owned fields of inherited records;
  - Variant wall additions / removals are preserved;
  - planar cut group selections → explicit `sources`; non-group `systems` ids are kept.
- **Model:** `model._own_cap` / `_cap_radius_of` — a wall's own end cap (`path.cap`), identity-preserving (a per-wall Full Round equals the project default byte for byte).
- **Golden:** 21 of 22 cases geometry-identical. `real_10xtest1` changed ONLY by the reported divergence: its Variant 1 round junction r 7 is dropped (§F4).
  - Proven: equal to the HEAD output with those Variant settings removed; its `.mudjob` is byte-identical to that reference.
  - Its snapshot was updated deliberately (`GOLDEN_UPDATE`).
- **Labels:** full hashes (`GOLDEN_STRICT=1`) differ for `designer.base` (7 cases) and `plan` (12) — record ids / `systems` labels only (WG1 → S1, `wall:<pid>`).
  - The `geom` hashes and every `.mudjob` full hash are equal.
  - To re-baseline after review: `GOLDEN_UPDATE=1`.
- **Gate (frozen copy):** 2498 passed + 1 xfail; toolpath 92. The 3 failures were the group-based UI smokes (assembly / simulation / Wall Systems), rewritten in M3.

### M3 — UI simplification (done)

- **Designer:**
  - the Wall Groups sidebar is replaced by **Junctions & ends** (`#walls-geom`): project junction shape + radius, end caps + radius, per-junction treatments;
  - every collapsed Paths row carries a compact **Wall Thickness** field (empty = THIN, with the material width: open 2W − R, closed W);
  - **multi-select** (Cmd / Ctrl / Shift-click) with a batch bar (thickness, alignment) — a temporary selection, not a group;
  - path Properties edit the wall itself (thickness, alignment, print reference, own End caps / End R);
  - a new thick wall defaults to **Centered**; stored alignments are never rewritten;
  - clicking a corner junction ◆ selects it again (Junction panel), the same in every layout state.
  - (`setWallProps` writes the owner record via `_catalogDocs`; one undo step; supersedes an in-flight resolve.)
- **Decision reversed:** 2026-10-14 "Wall Geometry belongs to Wall Systems" → junction / corner / cap SHAPE is architecture, edited in the Designer.
  - Reason (§0): these settings change the envelope (faces, material rings), not only lanes.
- **Wall Systems:**
  - assignment is a per-wall **checklist** (tick = assign; a wall has one system, ticking moves it), with each wall's thickness inline (writes the wall's envelope);
  - "+ All n walls without a Wall System";
  - no groups, no Variant scope;
  - the Wall Geometry panel is removed; construction only; the workspace order is unchanged.
- **Assembly:** the planar cut card gets **All walls / None** (explicit wall ids, one undo step) in place of "Wall Groups (all members)". Nothing else changed; no construction during editing.
- **Tests:** every `_wgNew` / group flow rewritten.
  - `ui_wall_system_smoke` covers: row thickness, thin width, Centered default, multi-select batch, per-wall cap, Junctions & ends, legacy adoption + migration;
  - `ui_wallsystems_smoke` covers: the checklist, one system per wall, mixed thickness, per-wall thickness undo / redo, Save / Open;
  - `ui_profiles_smoke` covers: All / None;
  - `ui_network_smoke` / `ui_view_smoke` cover: junction selection;
  - the `test_app` layout covers: no group panels, walls-geom in the Designer.
- **Golden:** identical to M2 (full hashes included).

### M4 — persistent junction records, SHADOW mode (done)

- **`design_proto/junctions.py`:**
  - `observe` — the contacts of a resolved document from `trim.contacts` on the processed source polylines (the same primitives as Trim / networks). Members = wall ids with roles `through` (+ arc hint `s`) / `start` / `end`. Kind = cross / tee / corner / hub.
  - `build_registry` — states in lineage order. An observation is matched to an existing record by member + role signature restricted to the walls present (a Variant removing one member of a 3-wall junction keeps the record, reduced), nearest hints. Unmatched → a new record owned by that state. Ids are kept from the stored registry; a junction no longer found anywhere is RETIRED (reported, id never reused).
  - `map_overrides` — each legacy corner key → the junction its production corner belongs to (`overrides` kept exactly; `shape` = one style if every corner agrees, else `per_corner`); orphan keys reported.
  - `consistency` — shadow vs production network nodes (agree / production_only / shadow_only).
  - `layer_presence` — per resolved Assembly layer: present / absent ranges, `max_move` (no limit, §F2), ceases / appears events (valid topology, §F1).
- **`layer_assembly/junction_shadow.py`** — the per-layer documents exactly as the envelope resolve builds them (`_resolve_stack`, `variant_request`, `lib.transformed`).
- **Read-only endpoint:** `POST /api/assembly/junctions_shadow {project}`.
- **Persistence:**
  - `project['junctions'] = {mode: 'shadow', counter, records}`, rebuilt in `project_file.validate` (on failure: the stored one kept + a warning);
  - the Designer carries it opaquely (`projectState` / `applyProject`), so ids survive Save / Open / Save.
  - **Storage choice:** PROJECT-level in shadow mode, so no document, payload or `.mudjob` byte changes. M5 was expected to move the records into the lineage-owned documents (§4) — it did NOT: they stay project-level (registry mode 'trims'); the move is still open (schema 8 / junction-shape ownership).
- **Production untouched:**
  - network.resolve records `junction_walls`; the model keeps `_production_junctions` / `_contact_sources` — internal attributes, never serialised;
  - stages 1–1b of `_build_effective` are extracted to `_contact_stage()` unchanged;
  - golden: full hashes identical to M2/M3 on all 22 cases.
- **Tests:** `layer_assembly/tests/test_junction_shadow.py` (35).
  - Covers: 2-wall / T / hub; two junctions of the same walls; mixed thickness + systems; stable ids across save, reshape, reorder; new / retired; Variant-added / removed walls and a reduced hub; override mapping + orphans; consistency on all 22 golden cases; moving, ceasing and appearing on transformed layers; planar cuts; no document / geometry change; the endpoint.
- **Consistency findings:**
  - every shadow junction exists in production (no `shadow_only` anywhere);
  - `production_only` appears in exactly 2 cases — `syn_branch_at_junction`, `syn_branch_across_trim`: C × R network nodes at (91.7, 40), where the circle's wall BAND overlaps the rectangle's band but the source lines do not touch (face / band contact);
  - all 5 real projects agree completely (base + variants);
  - `real_10xtest1`: its 3 junctions cease on the top layers (48–72) where the planar top cuts remove p5 / p2 — without the cuts every junction runs the full stack (verified);
  - real projects store no junction overrides (0 mapped).
- **Not implemented (M5+):** trims between junctions, source-line attachment, excursion routing (M7 host rule §F3 recorded only), contact-overlap changes, transform corrections.

### Correction pass after manual review of M0–M4 (2026-10-15; UI / ownership only, no geometry)

- **Paths list thickness control:** each row is `Thickness | − value + | unit`, with fixed widths so nothing overlaps.
  - The value is `THIN` (followed by the physical width, e.g. `5.25 in`) or the explicit thickness in inches.
  - The same control (`_thicknessControl`) is used in path Properties and the Wall Systems checklist.
  - It has its own buttons; it is not a numeric.js stepper.
- **THIN → explicit thickness rule:**
  - `+` from THIN goes to the first 0.5 in step AT OR ABOVE the THIN width (2W − R open, W closed): THIN 5.25 → 5.5; THIN 5.0 → 5.0.
  - `−` from that minimum returns to THIN; otherwise steps follow the 0.5 in grid.
  - A typed value below the THIN width becomes the minimum.
  - `setWallProps` applies the same floor, so every editing surface obeys it.
  - Stored values are never rewritten. One below the THIN width (an old file, or after a material change) is flagged ⚠.
  - The displayed THIN width follows bead width and return overlap immediately.
- **"Junctions, Ends & Corners"** (was "Junctions & ends"). Each control writes its own owner:
  - Junctions: project treatment + radius, and each junction's own treatment.
  - End caps: the project default + End R, and each open thick wall's own cap. A THIN open wall shows "U-turn (set by construction)".
  - Corners: each wall with source corners (rectangle / polyline) has Sharp | Rounded + Corner R, stored as its own `corner_radius` (Sharp = 0, Rounded defaults to 2 in). No new storage.
  - Path Properties' Corner R now goes through `setWallProps` (same field).
- **Wall Systems convenience editing:** an "Architecture · shared with Designer" section (dashed, visibly not construction) is built by the Designer's own builder (`window.Designer.buildArchControls`) on the SAME state.
  - Thickness stays inline in the checklist.
  - Edits are steps of the workspace's undo (snapshots now include each wall's cap and Corner R) and make the generation OUTDATED, since they are in the generation key.
  - Undoing back to the generated design is CURRENT again.
  - With a Variant open, edits go to the owner (Base) and the Variant gains no copy (tested).
- **Unchanged:** M4 junctions remain shadow only; no junction generation was touched (Parallel / Interleaved / Chained Loop junction geometry still poor, as expected at M4).
  - No backend file changed.
  - Golden: 23 passed.

### M5 — persistent junctions authoritative for TRIM BOUNDARIES (2026-10-15; reviewed, checkpointed)

- **Old failure (exact mechanism):** a Trim was a signature: the sorted SET of sources bounding its section at each end, plus the inside flags (`trim.Section.matches`).
  - A branch drawn to the circle × rectangle junction turned the boundary set `[R]` into `[L, R]`.
  - A wall crossing the removed stretch split it into two sections bounded by `[R]` / `[L]`.
  - Either way no section matched, the trim went `unresolved`, and the removed geometry reappeared (`syn_branch_at_junction`, `syn_branch_across_trim`, the old strict xfail, and the user's own `real_10xtest1` history: its trims p9 / p10 / p11 were re-made around such a branch).
- **Junction identity rule** (`junctions.compatible` / `_diff`): two member sets are the same junction when they share ≥ 2 walls (registry; equal roles) and one contains the other.
  - A member JOINING (a branch) or LEAVING (deleted, absent in a layout state) keeps the junction's id.
  - Exact matches are preferred, then the fewest changed members, then the nearest arc hint.
  - The registry (`build_registry` id keeping, per-state matching) uses this rule. Records accumulate members across states (a Variant-only wall is a member, restricted away in states without it). Registry mode is now `'trims'`.
- **Trim resolution** (`trim.resolve`):
  - The EXACT match runs first, so every trim that resolved before M5 is byte-identical.
  - Otherwise `_by_junctions` takes over. Each boundary is a contact on the source whose walls are `compatible` with the walls recorded at that boundary (restricted to the walls present). The removed interval runs between two such boundary junctions, with no other junction of those walls inside, whatever other walls touch or cross it; the `inside` flags pick the arc, and `u_mid` breaks ties.
  - Every current section inside the interval is marked trimmed (the UI greys them).
  - Status `ok` + `via: 'junctions'`.
- **Reporting:**
  - `inactive` — a boundary's recorded walls are all absent from the design (deleted / this Variant lacks them).
  - `unresolved` with the missing junction named — the walls are present but no longer meet there.
  - `ambiguous` — as before.
  - Nothing is ever re-attached elsewhere.
- **Persistent references:**
  - On save, each trim records its boundary junction ids (`trim.junctions = [J_a, J_b]`, from its owner state; `junctions.trim_links` / `annotate_trims`).
  - Resolution itself uses the identity rule, so a Designer resolve needs no registry, and the registry keeps ids by the same rule; they cannot disagree.
  - Records stay PROJECT-level ("Before M5" item 3 stays open).
- **Variants:**
  - A Variant-only branch joining a Base junction makes it a 3-member junction in that Variant: ONE record owned by Base. The inherited trims resolve there by junction; Base is unchanged; no Variant physical properties.
  - A Variant removing a member leaves a 2-member junction, the same record.
  - A Variant removing the trim's bounding wall makes the trim `inactive` there.
- **Topology through Z:** untouched. Assembly layers still use trims pinned to the authored resolution (`profile_cuts._pin_trims`); no M9 behaviour.
- **Production junction geometry:** untouched. Network junctions, motifs and construction are as before; only which source stretches are removed changes.
- **Diagnostics:**
  - `POST /api/assembly/junctions_shadow` now returns `trims` (per state: status, via, junction ids, members); `layers: false` skips the stack resolve.
  - The Designer has a "Junction identity" inspector (Junctions, Ends & Corners → Inspect): ids on the canvas, members, and each trim's junctions.
- **Consistency findings changed:**
  - The M4 C × R band-overlap `production_only` nodes in the two branch fixtures were the overlap of the UNRESOLVED trims; they are gone ("Before M5" item 1 no longer arises in any golden case).
  - Crossings ON a removed stretch are `shadow_only` (architectural junctions with no material — valid).
- **Golden (reviewed diffs, re-baselined):**
  - `syn_branch_at_junction`, `syn_branch_across_trim`: both trims now `ok`; the overlap removed (one perimeter); plan / `.mudjob` changed: the 18-layer job prints about 2,700 in less (14,598 → 11,888 in; 15,384 → 12,940 in).
  - `real_10xtest1`: ONLY the reason text of its 3 dangling trims (p10, p11: "its boundary junction with p5 no longer exists"; p9: its junctions no longer bound a matching stretch — p5 moved below the circle, and resolving would cut the real rectangle). Plan / `.mudjob` identical.
  - The other 19 cases are byte-identical.
- **Tests:**
  - `layer_assembly/tests/test_m5_trim_junctions.py` (11);
  - `test_trim_junction_ui.py` + `js/ui_trim_junction_smoke.js` (Designer UI against the real backend: Trim tool, branch, undo / redo, crossing, delete, inspector);
  - the strict xfail flipped to a passing regression;
  - `test_junction_shadow.py` consistency expectations updated.

### M6 — source-wall attachment (2026-10-16; manually reviewed and checkpointed (2026-10-16), with M7)

- **Old first divergence (symptom E):** the first stage where the authored relationship is lost is the wall NETWORK.
  - A branch's beads (its lanes, or its own faces) were built along its whole source line to the end it was drawn to — the host's SOURCE line — and joined whichever face bead that end touched (`network` face contacts).
  - For a Centered host the source line is mid-band, so nothing touches. For an Inside / Outside host (incl. legacy Auto = Inside on closed walls) the source line IS a physical face. A branch drawn to the wall from the other side therefore ran through the wall's material and SPLIT THE FAR FACE.
  - Reproduced on the user's `test 3` / `test 3 (1)`: their interior `Line 1` cut the rectangle's exterior face.
- **Attachment representation (architectural):** a branch END lying on another wall's source line, where that wall passes THROUGH (not its own open end, not a trimmed-away stretch), is attached to that WALL: `{branch, end, hosts, station (arc position on each host), side}`.
  - These are exactly the persistent junction's member roles (`start` / `end` × `through`, M4/M5). Never an offset-face id. Incidental crossings (a line passing through a wall) are never attachments.
  - Derived per resolved layer (Assembly layers re-derive from their own geometry; nothing stored).
  - Reported as `network['attachments']` only where a physical end was derived.
- **Physics (derived, `model._attach_branches`, stage 1d of `_build_effective`, after contacts / trims / junctions are found on the authored geometry):**
  - The branch's printed end = the host's BAND CENTRE at the station, reached along the branch's own direction. The band is the host's face offsets plus its source line when one-sided.
  - At a junction of several walls: where their band centres meet (the Centered junction point's analogue).
  - The wall network then meets the face on the branch's OWN side; the far face is never touched.
  - Derived ONLY where needed: Centered hosts (band centre = source line) and hosts whose source line IS the branch's near face (Inside / Outside with the branch on that side) — the authored end already meets the right face — keep it exactly (byte-identical).
  - (A first version also moved those ends; harmless for Hollow, but it changed the junction faces of a Skin + Web branch and broke cut-layer lattice coherence in `test_profiles_plane_trim` — caught by the gate, rule refined.)
  - Grazing (`|cos| < 0.25`) or too-short branches are `kept` and reported.
  - A moved START shifts the branch's openings / trims by the same arc amount, so they keep their XY place.
- **Verified:**
  - Inside / Outside × branch from interior / exterior × thin / 6 in thick: the far face intact, the near face joined.
  - A branch drawn to the wall prints EXACTLY like the same branch drawn to its near face for Hollow, Skin + Web, Interleaved, Parallel, Chained Loop and Linked.
  - Curved host (ring centre r 55); circle × rectangle trimmed junction with Inside / Outside hosts, branch inward (exterior ring unchanged) and outward (interior ring unchanged); M5 trims kept.
  - A branch on a trimmed-away stretch is not attached; Variant-only branch (Base untouched, no Variant physical property); mixed thickness + systems; openings on the branch and the host near the attachment; Save / Open (derived, never stored; the junction record names `B start` × `R through`).
- **Golden:**
  - `real_test3`, `real_test3b` (the user's E case): the exterior face is no longer cut. `test3b`'s plan / `.mudjob` change with it: the ring's Interleaved waves are RE-FITTED to the now-intact ring (the generator re-phases a ring on any envelope change; mostly sub-thousandth shifts, up to ~1.3 in in the transformed operator stack), and routes re-plan.
  - The operator's real `test 4` export (`test_view_fidelity`, dev machine only) predates this. The test now requires everything but the printed path to be identical (lifts, passes, layers, marks, bounds; print / travel length within 2 in — measured +0.13 in of 65,124 in). Re-export that job after review to restore the byte check.
  - The other 20 cases are byte-identical (`syn_mixed_thickness` is Centered — unchanged).
- **Not changed:** a branch line that protrudes PAST the wall (an authored crossing) still crosses it. Ends that lie on the true curve but not the sampled host line are not contacts (as before; the editor snaps to the sampled line).
- **Tests:** `layer_assembly/tests/test_m6_source_attachment.py` (37).

### M7 — continuous Out-and-Back excursions (2026-10-16; manually reviewed and checkpointed (2026-10-16), with M6)

- **Old behaviour:** a thin Out-and-Back branch meeting a GENERATED host (Interleaved / Linked / Parallel / Chained Loop) was extended straight THROUGH the host's paths and capped inside (`wall_systems.extend_through`). It joined the route wherever it crossed anything (the router merges nodes at crossings): any wave, any lobe. No host identity.
- **Now** (`wall_systems.splice_excursion`, first built on the `_splice` core of `connect_branch` — both deleted in the 2026-10-16 cleanup, the host search living on as `_resolve_host`; `model._connect_skins`, per built layer):
  1. **Host strand** = the generated strand BOTH lanes reach first, continuing straight along the branch (`_ray_hits`; both landings on ONE strand, close along it). It is resolved geometrically on THIS layer, never stored (§F3); Assembly layers resolve their own.
  2. That strand is cut between the landings p1, p2 (the short stretch between them). Its pieces keep its place, so every other strand keeps its id.
  3. The EXCURSION (p1, lane, U-turn, lane, p2) is its own strand `…x<k>` with `join_group = 'exc:<id>'` and `join_points = [p1, p2]`. The router may enter / leave it only there, so the route is host → excursion → the SAME host, continuing its trajectory. Every incidental crossing (another wave, a chain lobe, the host elsewhere) is a pass-over (the router's existing join-group rule, `toolpath_proto/graph.py`). Nothing global changed in the router.
  4. Reported per Wall System part: `junctions[] {excursion_from, host_strand, contacts, depth, excursion}`.
  5. **Fallback** (lanes reaching no single strand): the pre-M7 crossing loop, reported `status: fallback`. None occurred in any tested case. *(Replaced by the correction pass below: no crossing loop for an Out-and-Back; `status: unresolved`.)*
- **Per construction:**
  - Interleaved / Linked: the wave first reached.
  - Parallel: the exposed (innermost) lane only. The three lanes print as one polyline; nothing else of it changes.
  - Chained Loop: the chain run at the landing; a closed chain cut by two excursions prints as two pieces of that one strand.
  - Hollow / Skin + Web: the branch already joins the inner skin through the wall network — one circuit with it; unchanged by M7 (no crossings inside the band).
- **Width:** unchanged thin-wall model: the lanes are W − R apart (the branch 2W − R wide), checked for bead 2.5 / 3.0 / 3.5 and R 0.5 / 0.75 / 1.25.
- **Crossing is not connection (verified):** Interleaved stations where the excursion physically crosses another wave (x 14 / 20 / 71 …): zero graph nodes shared at its interior, and the crossed wave is BYTE-IDENTICAL to the same construction without the splice. Only the host strand and the old crossing loop differ.
- **Golden:**
  - `syn_excursion_interleaved`, `syn_excursion_chain`: excursions spliced and fenced; plan / `.mudjob` changed (route order at the excursions).
  - `real_test3b` (the user's Interleaved project): Line 1 is now a fenced excursion; 0 travels.
  - `syn_excursion_skin`, `syn_mixed_systems` are unchanged (Hollow host; no Out-and-Back into a generated host).
- **Tests:** `layer_assembly/tests/test_m7_excursions.py` (24): every generated construction straight + curved, Parallel exposed lane, Skin + Web / Hollow, crossing ≠ connection (the crossed wave byte-identical), multiple branches, near a corner, near a trimmed junction, Variant-only, bead / R, per-layer host, PrintPlan / `.mudjob`.
- **Supersedes** the pass-10 (2026-10-07) "meet by crossing, nothing of the host cut" rule for OUT-AND-BACK branches only. Other incoming walls (hollow, generated) still meet by crossing. `test_wall_systems.py::test_t_junction_walls_touch_by_crossing[*-single-*]` now asserts the excursion; the routing assertions (one run, no travel / retrace) are unchanged and pass.
- **Operator project (`test 4` export):** its Line 1 is an Out-and-Back into the Interleaved host. Each layer's old crossing loop (~12 in) became a short excursion, so the print is ~0.25 % shorter; the relaxed structural fidelity test allows that.
- **Not done (later milestones):** contact overlap at the landings (M8); transform-junction resolution (M9); excursions for thick (non-thin) incoming walls (they still meet by crossing — the pass-10 rule; `connect_branch` is gone).

### M7 correction pass after manual review (2026-10-16; manually reviewed and checkpointed (2026-10-16), with M6 + M7)

- **Observed in review:** a thin Out-and-Back running THROUGH a Chained Loop host showed bracket / staple geometry at the crossing; Base and Variant / later layers differed; Chained Loop excursions came and went with the pitch.
- **The bracket = the pass-10 crossing loop** (`wall_systems.extend_through`): lanes continued past the host's face and capped inside, crossing the host's paths. M7 used it as the fallback whenever the host search failed. Two failures fed it:
  1. **Sequential splicing.** The arms of one generated part were spliced one after another, each on the geometry the previous ones had cut. A through wall's two arms (the network splits the line at the host) meet the host at the same station from opposite sides. On a Chained Loop both often reach the SAME stretch: the second arm then found only the first arm's cut, failed, and got the crossing loop. Fallback loops were also appended to the geometry later arms searched.
  2. **The Parallel-kiss guard.** `_splice` refuses any host stretch holding a vertex the strand visits twice. A Chained Loop's necks are such vertices (in multi-ring / junction layouts, e.g. circle × rectangle — 51 in the synthetic case, 48 in the user's `test6`). When a neck fell between the lanes, every candidate was refused.
- **Why Base / layers / pitch differed:** the loop pattern re-fits to every envelope change: pitch, a Variant opening, an Assembly cut. That moved the necks relative to the branch and changed which stretch each arm reached. The architecture never changed; the old search's outcome did. Reproduced on a copy of `test6` + a vertical line (old code: Base excursion, Variant bracket at pitch 7; the ending branch also failed at pitch 6). The new code gives excursions on Base, the Variant and every Assembly layer.
- **Now (`wall_systems.splice_excursions`, `model._connect_skins`):**
  - All Out-and-Back arms of a part are resolved TOGETHER on the unspliced generated geometry (`_resolve_host`, the unchanged host search of `_splice`), so the result is order-independent.
  - The kiss guard is not applied to excursions. Cutting through a neck is valid: each piece stays one polyline, and the other pass keeps the vertex. The guard survives only as `_resolve_host`'s `touch_guard` option (off for excursions; `connect_branch` was deleted 2026-10-16).
  - Each host strand is cut ONCE for all its landings (`_compose_cuts`): landings sorted along the strand are paired alternately, so every landing keeps exactly one host end. A closed strand uses the pairing that removes less.
    - One excursion → its short stretch, exactly as before.
    - Two arms on the same stretch from opposite sides → the stretch between them is KEPT: host → excursion A → back along it → excursion B → host, every bead once. Coincident landings are snapped to one exact point (one routing node).
    - A nested landing pair (would split the strand) refuses the later arm, reported.
  - Host pieces keep the old numbering (strand order from the first branch's cut).
  - An arm with no host is REPORTED (`status: unresolved`) and keeps its own beads. **No crossing loop for an Out-and-Back.**
- **Through-wall semantics:** a thin wall crossing a host = the architectural crossing (one `cross` junction record: wall through × host through). The wall network already splits the line at the host into two arms, one per side. Each arm is a local excursion of the strand IT reaches first from its side. Interleaved / Linked / Parallel: the innermost and outermost wave / lane (other strands byte-identical). Chained Loop: the one chain strand, twice. Hollow / Skin + Web: the network joins each arm to its side's skin (unchanged). Incidental crossings remain pass-overs.
- **Legacy rules — audit:**
  - `extend_through` crossing loop (pass 10): SUPERSEDED for thin Out-and-Back arms. Still used for other incoming skin-type walls (Hollow / thick single) meeting a generated host.
  - `wall_system_crossing` bridge caps (`~cross`, `_build_effective` skin-part chords): a skin wall BRIDGING generated walls (both ends on a host — a thin partition, or the inner segment of a line crossing a room completely). Not an Out-and-Back (no free end); UNCHANGED, deferred to the wall-to-wall junction work.
  - Contact staples (`_contact_staples`, generated wall ↔ generated wall, full-wall), `wall_system_mouth` (generated wall into a skin host), `_splice_skins` mouth splice (no straight lanes), the Chained Loop eye check: unchanged, never applied to an excursion-owned arm.
- **Golden:** no change (all 23 strict-identical to the M6 + M7 baseline). `syn_excursion_chain`'s two excursions on one chain strand are composed in one cut. The pieces keep their numbering, so they are byte-identical.
- **Tests:** `layer_assembly/tests/test_m7_through_walls.py` (30). It covers:
  - ending branches;
  - through walls on every generated construction, straight and curved;
  - each side taking its own exposed wave;
  - Hollow / Skin + Web through walls;
  - Base, Variant and every Assembly layer sharing one topology (pitch 6 / 7 / 9);
  - Chained Loop pitch sweep (loop counts change; neck-straddling cases included);
  - Interleaved / Linked parameter sweeps;
  - crossing a wave and rejoining its own host;
  - M5 trims, M6 attachment, Variant-only and PrintPlan / `.mudjob`.
- **Deferred:**
  - Thin wall-to-wall bridges (above).
  - Parallel Walls polish: prefer the lane-transition seam away from junctions / corners / openings where practical (not a correctness issue).
  - Full-wall ↔ full-wall junctions (circle + rectangle both carrying complex systems): separate architecture discussion, never patched with M7 excursions.

### Cleanup after M7 (2026-10-16; behaviour-preserving; manually reviewed and checkpointed 2026-10-16)

- **Removed from production** (zero callers verified, strict golden 23/23 byte-identical (no re-baseline), PrintPlan / `.mudjob` identical): `PrintLayer._connect_generated` / `_kiss_into_skin`, `wall_systems.connect_branch` / `_splice` (M7 never used them; `_resolve_host`, `_sub`, `_trim_from`, `_lane_start`, `_ray_hits` kept), the pairwise Zigzag / Wave lattice, `Path.wall` / `WallSpec.system`, `NetworkWall` / `network_walls`, the in-page migration, `app.js addOffset`, `route_quality.wall_metrics` (test-only → `tests/wall_metrics.py`).
- **Migration boundary:** `walls.migrate_project` converts every pre-schema-7 form once (incl. Variants' added paths) and no longer writes empty `offset_treatments` or the removed collections. `DesignLibrary` always composes; `designer_source.grouped()` is gone.
- **Milestone namespace:** M-numbers in this document (M0–M10) are not those of `DEFERRED_TOOLPATH_ARCHITECTURE.md` (deferred print paths M0–M5; M0–M2 done, M3+ archived).
- **Deliberately NOT done here** (each its own reviewed step): junction override removal, `trim.junctions` write-only, M6 attach unification, `tolerances.py` / Vec2 consolidation, schema 8, the pass-10 contact machinery, full-wall junctions, M8, M9.

### Small fixes after the cleanup (2026-10-16; uncommitted, pending review)

- **Support / header frames under transform groups:** `support.group_at` compares a finding's / header's local point with each vertex in that vertex's OWN group frame (it compared it with the already-moved variant geometry: a wall moved away by its group lost its header to a nearer unassigned wall). A generated header's initial span uses its wall's placement scale. Test `layer_assembly/tests/test_support_group_frames.py`.
- **Webs fill only their own Wall System:** a web owned by a Skin + Web Wall System is generated on its walls' envelope, other thick walls (Hollow, a wall in no Wall System) closed off at their mouth (`model._restrict_webs` / `_web_envelope`, the `_split_wall_systems` stitching). A thick wall in no Wall System no longer receives the neighbouring web. Thickness and membership stay independent state (UI smoke). Test `layer_assembly/tests/test_wall_membership_web.py`.
- **THIN width verified** (no code change): lanes W − R apart, deposited 2W − R, overlap R. Test `design_proto/tests/test_thin_width.py`.
- **SHOW BEAD view** in Wall Systems and Print Simulation (drawing only, project bead width).
- **Still OPEN (deferred, strict-xfail reproductions):** an unassigned one-end-attached Out-and-Back rotates through Z with its host's group (`test_transform_unassigned_wall.py`); Skin + Web phase incoherent through Z on a gradually scaled circle (`test_wall_system_z_coherence.py`); an Assembly opening changes far geometry of GENERATED constructions (Interleaved / Linked / Chained Loop / Parallel; Skin + Web and Hollow are local) — no test yet. None was introduced by the cleanup.
- ~~**Still OPEN — physical wall width**~~ — DONE, see "Physical wall width" below.

### Physical wall width (2026-10-16; manually reviewed, checkpointed)

- **Contract (user, 2026-10-16):**
  - Wall Thickness = FINISHED DEPOSITED width (outside bead edge to outside bead edge), never centreline spacing; the architectural envelope stays authoritative and unchanged — centrelines go inside it.
  - Bead Width = one pass; toolpaths are bead centrelines.
  - Contact Overlap O = overlap where a path intentionally lands against another (W − O); explicitly includes web → skin; never ordinary crossings.
  - PARALLEL WALL OVERLAP (user-facing name of `return_overlap`, key kept) = the MAXIMUM overlap of neighbouring paths running alongside each other (THIN lanes, Parallel lanes); a maximum, not a target.
  - THIN = 2W − Parallel Wall Overlap (derived; 5.25 in at W 3 / 0.75). (The user's first statement used O; corrected to the parallel-lane rule — O and R stay separate concepts.)
  - Center / Outside / Inside are AUTHORING references; downstream topology should eventually use canonical derived wall geometry (not done, below).
- **Audit (read-only, before the fix; T 7, W 3, O = R = 0.75):** generated systems deposited ≈ 7 (centrelines ½ bead inside the faces, `H0 = T/2 − W/2`); Hollow / Skin + Web deposited 10 (faces printed as centrelines); THIN 5.25 (from R, not O). The SHOW BEAD renderers were faithful; the "too narrow THIN" was the 10 in Hollow / Skin + Web reference wall. Only Skin + Web landings used O; Linked closest approach 0 (full overlap), Parallel spacing (T − W)/(N − 1), staples / push reach / `~cross` / `extend_through` depths constants, generated band contacts effectively O = 0 — all left for M8 / junction work. No single canonical centreline exists: junction / trim detection runs on the AUTHORED source lines (an Inside / Outside wall's source is a finished face); `envelope.centrelines` holds the authored line.
- **Fix:** `WallSpec.offsets(inset = W/2)` for `model.SKIN_SYSTEMS` (Hollow, Skin + Web; unassigned thick = Hollow) in physical, non-`envelope_only` builds: Center ±(T − W)/2; aligned W/2 and T − W/2 from the authored line (now reference-only). `INSET_MIN_SPAN` guards stale thicknesses below two beads.
- **Local adaptations (no junction architecture):** `NetPiece.ref` (aligned reference kept for junction finding, out of `walls`) and `NetPiece.arch` / `arch_band` (finished faces) in `network.find_attachments`; caps / opening cap reach exclude an outside reference (`_wall_system_end_pts(include_src=False)`); `_apply_networks` unions walls attached to an inset wall even when beads no longer touch; M6 `_attach_branches`: an end on an inset host's finished face on its own side → the near printed skin (else band centre as before); `_split_wall_systems` push depth uses `_print_thickness` for an inset host.
- **FAST at the checkpoint: 20 KNOWN FAILURES (not hidden, not re-baselined):**
  - envelope ≠ full-build regions for skin walls (`test_deferred_print_paths` ×2) — the documented "envelope rings = full-build regions" invariant needs a decision (equal for generated walls; skin walls get the inset band);
  - Route Origin on an aligned skin wall's reference strand → `missing` (`test_route_origin` ×4) — re-attach to `<id>.wall_near` vs the "never moved onto other geometry" rule: decision needed;
  - reference network with Skin + Web hosts (`test_wall_systems` ×7): a Hollow / Skin + Web connector leaves the courtyard pocket a second run (the lattice treats courtyard + outside as one ring; only a fragile exact landing joined them), Skin + Web line into Parallel host, generated lines no longer crossing the curved Skin + Web circle — mixed-junction machinery, deferred to the full-wall junction work;
  - not yet investigated (may be expectation updates): `test_source_attribution::test_at_a_T_the_host_keeps_its_full_thickness`, `test_semantic_transform` ×2 (connector), `test_support_contract` bridge width, `test_wall_membership_web::test_ticking_the_wall_gives_it_the_web`, `test_profiles_designer` taper coordinates, `test_route_planning` travel.
- **Golden (not re-baselined):** expected to change — `real_10xtest1`, `real_test3`, `syn_branch_across_trim`, `syn_branch_at_junction`, `syn_circrect_hollow`, `syn_excursion_skin`, `syn_mixed_systems`, `syn_mixed_thickness`, `syn_profiles_curved/linear/stepped`, `syn_transform_multi/whole`, `syn_variant_walls`; expected identical — the 8 generated-only cases.
- **Product questions:** Parallel Walls default 3 lanes in a 7 in wall overlap 1.0 in > 0.75 max (lane-count rule vs report); Skin + Web web needs T ≥ 3W − 2O (7.5 in) to keep W − O off both skins. *(The second superseded by Pass A below: no minimum thickness; the web is angle-driven, floor-limited below.)*
- **DEFERRED (recorded, not fixed here):**
  - authoring alignment must eventually NOT restrict connectivity: a wall must be able to attach to EITHER physical face of another wall regardless of Inside / Center / Outside;
  - canonical derived wall topology / centreline — with the full-wall junction work;
  - Skin + Web / Wall System through-Z pattern coherence — a separate issue;
  - end caps / opening jambs still centre a bead on the cut / end line (W/2 beyond along the wall);
  - M8 contact-overlap migration (Linked, staples, generated contacts, mouths, bridges).
- **Tests:** `design_proto/tests/test_physical_wall_width.py` (58); geometry expectations updated with comments in `test_physical`, `test_junction_contact`, `test_corner_motifs`, `test_reference_network`, `test_wall_systems` (fixtures widened by W where a test was written for 10 in skin spacing).

### Skin + Web single-layer correction — Pass A (2026-10-16; manually reviewed, checkpointed; FAST: the same 20 known failures)

- **Why:** the lattice diagnosis (same day) found the curved-wall / through-Z instability starts on ONE layer: false / flickering / sliding corners on curves, and a flat web in narrow cavities (fixed 20 in pitch). Pass A fixes those; cross-Z tracking (Pass B) is untouched.
- **Product decisions (user, 2026-10-16):**
  - a continuous curve is never a corner merely because it is tight; a circle has zero corners at any radius; no radius threshold; rounded architectural corners / junctions are NOT redesigned in this pass;
  - Skin + Web is TARGET-ANGLE driven: pitch derived from the cavity; no arbitrary minimum thickness; Contact Overlap landings kept;
  - short territories (piers): NOT solved here. Approved rule for Pass B: a shrinking territory's web DESCENDS from the pattern below (no new phase; the last surviving brace keeps its diagonal) and branches back out from it as the territory grows — stack-aware, with the reference / tracking machinery.
- **Corner detection** (`wall_lattice.corner_stations`, now shared by the lattice and `wall_systems._corners`; the Adaptive Truss uses it too):
  - root causes found: the 3 t / 6 t share test wrapped headings at ±180° (a tight ring's 6 t window read as a small turn → false corners for R ≲ 1.6 × band); it was skipped within 3 t of open-run ends; opposite-sign junction kinks inside the window cancelled an arc's flank turn (lobe arcs read as concentrated); the cross-sign local-maximum test let an arc's reading hide an adjacent kink (kinks flipped in / out with 2.5 % scale steps);
  - now: unwrapped heading; SAME-SIGN turn readings (left / right summed separately, 0.5° per-sample dead-band so straight legs read exactly 0 and sharp-corner plateaus are unchanged); candidate ≥ 30° one way within 2 t, a same-way local maximum; CONCENTRATION = core (3 t) mean curvature ≥ 3 × the flanks' (1.5 t each) mean same-way curvature (equivalent to the old 0.75 share for symmetric flanks); a flank is clipped at run ends and at a junction kink (≥ 15° the other way); one clipped flank → the whole other flank decides; both clipped → each measured from the outer half of the core (an arc filling the core between kinks reads as a curve); anchor unchanged (first sample of the maximum);
  - measured: circles R 8–40 at T 10 / 12 / 16 → 0 corners (old: up to 3); open arcs 0 (old: a false corner at R 30 / 180°); the circle × square lobe building T 10 / 12 / 16 at scales 1.0 → 0.675: constant 5 corners (3 square corners + 2 junction kinks; old: 5 / 4 / 3 flicker at T 12, a corner sliding round the arc at T 16). At T 16 / scale 0.65 the kinks fall under 30°: a genuine corner-angle change.
  - motif construction guard: the single-pass motif's outer wrap (Oa → Ob quadratic) is only used when Ob lies ahead of the rails' meeting point; else the straight chord (a kink that a tight arc turns straight back gave a self-crossing hairpin).
- **Target Web Angle** (Skin + Web zigzag / wave; `wall_lattice.web_pitch`, `plan(web_angle=)`):
  - angle measured from the wall line, across the CAVITY between the contact rails: w = skin-centreline separation − 2 (W − O); S = w · cot(angle); the separation is MEASURED (length-weighted median of the runs' face distance), so pitch follows the actual wall; integer stitch fitting, corners, tracking unchanged — S just replaces the fixed target;
  - floor S ≥ max(1, W/2) (consecutive landings on one skin a bead apart); reported `limited` with the effective angle (T 8 / W 3 / O 0.75: cavity 0.5 in → 1.5 in pitch, 18.4°);
  - DEFAULT 20° (= the long-standing 20 in pitch in a 12 in wall measured skin to skin before the physical-width fix, 20.6°); range 10–75°; the skeleton is sampled as for the old 20 in target (a tight derived pitch must not refine it into fragments); the wall / area test keeps max(S, 20);
  - straight wall, default: T 8 1.5 in (limited) · T 9 4.1 · T 10 6.9 · T 12 12.5 · T 16 23.1 · T 20 34.2 in; measured stitch angle 19.8–20.2° (old fixed 20 in: 1.5° / 4.4° / 7.3° / 13.0° / 22.7° / 31.9°);
  - only OWNED Skin + Web webs (`RegionInfill.owner`) are angle-driven; unowned legacy wall infill keeps Target Spacing; truss unchanged (its Brace Angle).
- **Curves — the CONCAVE skin owns the angle (correction after first review, 2026-10-16; reviewed):** on a curve each brace leaves the concave (inside) contact rail at the angle to that rail's LOCAL tangent and runs STRAIGHT to the convex rail; the convex landing position and angle follow (no equal spacing on both skins). Local, any smooth curve: `_Geo.table` gets u = ∫ a_straight / a ds, a(s) = the station advance of that ray to the convex rail (forward and backward, averaged; concave side = the rail advancing less, sample by sample, so it switches continuously at inflections; φ is an integral — no phase reset); zigzag braces in curve zones are straight. End zones (1 t from a run end), corner zones (2 t round a detected corner), non-perpendicular spots and anything within 1e-6 of straight keep the reviewed rule exactly, so straight walls, sharp corner motifs and junction-adjacent stations are byte-identical. Measured (T 12, 20°): concave departure median 19.2–21.7° from R 200 to R 10; convex arrival 23° (R 200) → 53° (R 10); convex landing spacing up to 1.6 × the concave; landings evenly spaced round the ring. Converges slowly to straight (R 200 −6 % pitch, R 2000 −0.6 %: a flat brace is long against the cavity). Loops need an even whole number of stitches, so on very tight rings (8–10 stitches) the angle is off by up to ~3°.
- **Saved projects:** load unchanged. Every saved Skin + Web web stored `params.spacing` (20); it is kept in the record but ignored for Skin + Web; a missing `web_angle` = 20°. No migration (a pitch → angle conversion depends on each wall's thickness, and the old default 20 in was never a chosen value). New webs store `{web_angle: 20}`.
- **UI:** the Skin + Web web panel (Designer + Wall Systems) shows Target Web Angle (°) instead of Target Spacing, a note that pitch follows angle + cavity, and the readout "Pitch: … in (Target Web Angle …° across a … in cavity)", plus "angle limited" when the floor binds. `/api/infill_patterns` adds `web_parameters`.
- **Other Wall Systems:** Interleaved / Linked / Chained Loop / Parallel change ONLY where the shared false-corner bug hit (tight circles, R ≲ 1.6 T; measured on a 28-fixture set: lines, sharp / rounded rectangles, R 20–60 circles byte-identical). Adaptive Truss: false corners on circles removed; a Corner-R 20 rectangle at T 10 (fillet ≈ 4.5 t long) is now a curve as in Zigzag / Wave (truss used to skip the concentration test entirely) — flag for review.
- **Known / open:** contact violations at caps and rectangle corner motifs on thin walls are pre-existing (unchanged counts except T 8 line 1 → 2); a seam-corner transfer hook (top-right of the lobe building) is pre-existing; at very thin cavities the floor gives a dense web (T 8: 236 stitches on 360 in) — a product question (floor = W/2 or a Parallel-Wall-Overlap rule?); a mixed-thickness region uses ONE derived pitch (its median cavity), not per-run.
- **Golden (not run, not re-baselined):** expected to change — every case with a Skin + Web web (pitch); generated-only cases only where a curved run had false corners (likely candidates `syn_circrect_interleaved` / `syn_circrect_chain`; not verified).
- **Tests:** `design_proto/tests/test_skin_web_single_layer.py` (92: A circles analytic + built, B open arcs, C lobe sweep + kink hairpin, D straight-wall angle, E pitch vs cavity, F tight circles, G generated systems, compatibility); `layer_assembly/tests/js/ui_wallsystems_smoke.js` Target Web Angle block.

### Before M5 — to decide

1. *(M5: no longer arises in any golden case — those nodes were the unresolved trims' overlap; still open for genuine band overlaps without trims.)* **Band-overlap contacts** (production_only above): with source-line junctions, bands that overlap without their source lines touching are NOT a junction. Should M5 treat them as plain material union (no junction, no corner treatment), or keep a junction there?
2. **Per-corner shapes:** §4 has one `shape` per junction; legacy overrides are per corner (no real project uses any). Is one shape per junction enough? M4 keeps the per-corner list.
3. **Record storage:** move the records into the lineage-owned documents (as §4) — confirm. *(M5 did not need it: trims resolve by the identity rule and record the ids; records stay project-level.)*
4. ~~Re-baseline full golden hashes~~ — DONE at the M0–M4 checkpoint after the user's review. Before it, every strict difference was a full hash only; the label-stripped `geom` hashes and `.mudjob` were equal. `GOLDEN_STRICT=1` now passes 23/23.
5. **Junctions that APPEAR only on transformed layers** stay unnamed (per-layer topology, §F1) — confirm that no record or treatment is wanted for them.


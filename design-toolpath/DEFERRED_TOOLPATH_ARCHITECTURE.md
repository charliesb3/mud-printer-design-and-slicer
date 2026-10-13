# Deferred Toolpath Architecture — Assessment

Status: assessment 2026-10-11. **M0 + M1 IMPLEMENTED 2026-10-11 (uncommitted, pending manual
review)** — see "Implementation status" at the end; M2 partly (bead-level findings shown in the
Simulation); M3 done 2026-10-11 (see "M3 done"); M4–M5 not started.
Context: `PROJECT_OVERVIEW.md`, `PROJECT_MEMORY.md`, `layer_assembly/PROJECT_MEMORY.md`.

## Question

Can lightweight ARCHITECTURAL geometry (shapes, wall envelopes, junctions, trims, openings,
Assembly transforms / profiles) be resolved in the Designer and Assembly without Wall System,
lattice and routing generation, and can those be deferred to a print-preparation stage that
sees the complete assembled building?

**Answer: yes, in stages.** The envelope already exists as an internal stage of the Designer
build and costs about 1/90 of a full build. Deferring lattice and routing is a moderate
refactor that keeps every toolpath identical. Making the lattice coherent across the whole
stack is a separate, harder generator project that should come second.

## Measurements

These are from the 84-layer `layer_assembly/bench_mixed.py` building, run in one process on a
12-core Mac. They were taken with throw-away scripts that monkeypatched the code in memory
only; no production code was changed.

| | Cost |
|---|---|
| Full variant build (today, shared references warm) | ~3.9 s CPU / layer |
| Shared references (first variant per design) | +14.5 s |
| Envelope only: cuts + semantic transforms + trims + faces + openings + junctions / fillets → material rings | **43 ms / layer (3.6 s for all 84 layers)** |
| Exact Assembly edit today (browser, 8 workers) | 11.9 s (opening width) – 43.7 s (plane / transform / array) |

Where one variant's time goes (cProfile, 12 variants, 47 s in total):

| Stage | Time | Share |
|---|---|---|
| `_apply_wall_systems` | 33 s | 70 % |
| of which `_split_wall_systems` source tagging (brute-force `dist_to_polyline`) | 13 s | |
| of which `wall_systems.generate` | 10 s | |
| `wall_lattice.plan` | 4 s | |
| routing-graph build / closure checks | 4.4 s | |
| `_apply_networks` (junctions) | 2 s | |

Each cut variant runs `_build_effective` about three times: the uncut Wall System reference,
the scaffold closure verdict, and the build itself. The envelope work is repeated each time.

## 1. Can architecture resolve without lattice or routing?

**Yes, internally, but it is not exposed.** In `model.PrintLayer._build_effective` (model.py
~2731), the stages run in this order:

1. source paths, then trims (`trim.sections` / `trim.resolve`);
2. wall offsets / faces;
3. openings (`_OpeningPlan`, doorways) and mouth cuts;
4. wall networks (`_apply_networks`: joins, junction corners, fillets, material components).

At model.py ~3103, `regions = network.pop('_rings')` holds the complete material rings
(polygons with holes, per material component) together with the infill jobs. Wall Systems
(`_apply_wall_systems`), the wall lattice, the field fallback and solid fill all run after
that point. Routing (`to_routing_layer` → `layer_route` → `graph.route_layer`) is a separate
later pass.

Assembly inputs act before this point:
- Profile cuts (`profile_cuts.apply_cuts`) and semantic transforms
  (`semantic_transform.transform_document`) are applied to the DOCUMENT before resolution
  (`DesignLibrary.transformed`).
- Openings, tapers and planar cuts are therefore already architectural.

There are three caveats:
- Legacy `LatticeInstance` infills are generated before the network stage. They become
  network elements, so their envelopes would need a small special case.
- Single, Hollow and Skin walls are face beads produced by offsetting: for them, envelope and
  printed faces are the same geometry, which is cheap.
- No function returns this cut-off today. `network_summary()` and every other caller run the
  full build.

## 2. How Wall System generators could consume assembled geometry

| Generator | Inputs today | Consumes resolved geometry? |
|---|---|---|
| Parallel / Interleaved / Linked / Chained Loop (`wall_systems.generate`) | material rings + own skeleton (`WL.skeleton`), `phase_ref` (uncut `ws_frames`), `transitions`, `junction_ends`, `seam_hints` (Route Origins) | **Yes** |
| Corner / junction motifs | strip frames derived from the rings | **Yes** |
| Skin + Web web, Adaptive Truss (`wall_lattice.plan`) | rings + stations + track / track_map + lineage scaffold | **Yes** |
| Mixed-region split, skin joins, contact staples (`_split_wall_systems`, `_connect_skins`, `_contact_staples`) | rings **plus** per-edge source identity, original face lines (`_src_faces`), centrelines, source order | Only if the envelope carries per-edge source tags |
| Single / Hollow / return lanes | offsets of the source centreline | Nothing to defer (already envelope-level) |

Generation is route-aware, so these decisions move together with the lattice into print
preparation:
- pass multiplicity chosen by route inspection for parity (`wall_lattice.py` ~1482–1679);
- field-fallback repair against the wall graph;
- closure verdicts (`scaffold_verdict` / `_open_ends`) deciding planned-alone, the
  `_cut_join` fallback and station re-plans;
- seam hints.

## 3. Coherent lattice across the stack

Today the lattice is solved per variant and then repaired:
- one lineage scaffold per family and transform spec;
- tracking against the UNTRANSFORMED reference plan, not the layer below;
- Assembly opening positions injected as `station` ops on every layer, with
  `_station_reference` re-planning a shared reference when a probe cut cannot close;
- cut variants clip the uncut lattice and join locally (`_cut_join` / `_join_cut_ends`), or
  fall back to a re-plan.

Station ops are a workaround for the generator not knowing the stack. They are also why an
opening move rebuilds every layer.

A stack-aware generator would work in four steps:

1. **Collect** each WALL RUN of the assembled building, identified by source and system and
   measured wall-relatively (arc length along the source). For each run, gather every opening
   jamb, cut end, plane termination, junction and transform over all its layers.
2. **Solve once per run:** stations, pass parity, phase, period and seam. These are the
   decisions `wall_lattice.plan` and `wall_systems.generate` already make, given the union of
   constraints up front.
3. **Emit per layer:** clip the run's solution to that layer's rings and join locally at cut
   ends. This is today's `_cut_join` / `_join_cut_ends`, which then rarely needs a fallback.
4. **Junction territories:** carry the reference junction geometry wall-relatively from layer
   to layer (morph with the owning walls). Re-plan only where junction topology changes, with
   a dedicated shrinking-junction motif. This addresses the documented main open problem
   ("Transformed-junction lattice").

**Reusable:**
- skeleton / Delaunay;
- run planning, stitches and motifs;
- tracking and parity repair;
- jamb-station doubling;
- the clip + local join;
- `phase_ref` frames;
- the closure check;
- `source_attribution`.

**New:**
- the stack-level run-identity and constraint collection;
- a per-run solution object;
- an unsupported-lattice search that first tries alternative local solutions (other motif,
  phase shift, doubled pass) before reporting.

## 4. Lightweight geometry: what the envelope layer carries

Per layer (design + transform spec + cut spec, the same cache key as today):
- material rings, with holes;
- the source id of each ring edge;
- each source's processed and transformed centreline and thickness;
- Wall System membership and settings;
- junction records (sources, kind, present);
- openings (id, source, edge, at, width, jamb lines; Designer and Assembly alike);
- cut / plane end walls;
- trims (as resolved);
- Single / Hollow face beads (they ARE the envelope).

Planar cuts, self-supporting openings, arrays and tapers are already source-level cuts. They
need no new representation.

## 5. Envelope-level vs bead-level support

| | Envelope check (editing) | Bead check (print preparation) |
|---|---|---|
| Geometry | upper material region vs lower material region | upper bead capsules vs union of lower capsules (`support.py`, unchanged) |
| Rule | overhang of the wall body ≤ Maximum overhang; openings are holes, so a span with nothing below = HEADER NEEDED | every bead ≥ required overlap; empty-corridor bridge rule |
| Catches | lean, taper, scale, opening closures, plane steps, missing headers | lattice misregistration, stitches over cavities, end / jamb motifs, Interleaved / Chained period shifts, junction drift |
| Role | fast feedback; never authoritative for printing | authoritative; findings first trigger alternative local lattice solutions, then are reported |

Points to keep in mind:
- The meaning of the editing check changes deliberately: it now checks the wall body's
  overhang, not each bead's overlap.
- The bead checker is not weakened, and its result is final.
- Lattice findings will appear later than today, in print preparation instead of during
  editing. That is a user-visible change.

## 6. PrintPlan and Simulation without a second toolpath

- Print preparation emits the same `LayerGeometry` objects (polylines + `src` tags +
  routing diagnostics) that `print_plan.build_plan` already routes; the plan and stream are
  unchanged.
- The Simulation consumes only the stream, so it is untouched.
- The Assembly preview draws envelopes, which are architectural geometry, not a toolpath.
- An optional "prepared layer" view reads the prepared `LayerGeometry`.
- The prepared result is cached under the existing keys. There is ONE authoritative
  printable representation: print preparation's output.

## 7. Recent performance optimizations — keep all of them

| Optimization | After the migration |
|---|---|
| Number-type-insensitive `cut_key` / `spec_key` | still keys every variant / prepared layer |
| `lifts._near` / `SegIndex.within` / `layer_components`, one-component router | the plan is unchanged |
| `support._SegIndex.within` | the bead-level check is unchanged |
| Cancellable `prefetch` + plan polling | print preparation runs in the background; essential |

- None becomes obsolete.
- All are exact and covered by `tests/test_performance_pass.py`.
- There is no technical reason to revert to the pre-performance checkpoint.
- One further exact optimization is available under either architecture: a spatial index
  for the `_split_wall_systems` source tagging (~28 % of variant time). Not done.

## 8. Preserving project files and the working pipeline

- **Project files** (`project_file.py`, schema 1) store authoring state only: Layer
  Designs, the Assembly state, Wall System assignments and parameters, lineage lattice
  definitions and Route Origins. Nothing derived is stored, so deferral needs no schema
  change.
- **Wall System assignments** stay in the Designer documents; only their geometry
  generation moves.
- **Run both paths side by side.** The current full build stays the reference
  implementation until the new path is at least equal. Every milestone below either produces
  byte-identical toolpaths or is compared against the old path on these fixtures:
  - `tests/reference_network.py`;
  - `bench_mixed.py`;
  - the profile / plane suites.
- **Keep the Designer view on the full build.** Editing ONE design there routes in about a
  second. Only the Assembly's whole-stack edits need the fast path.

## Main architectural obstacles

1. **Source identity at the envelope.** Mixed-region Wall System splitting needs to know
   which source owns each ring edge, plus the original face lines. The envelope must carry
   per-edge source tags. They are derivable from the face beads present at the cut-off.
2. **Route-aware generation.** Parity, closure verdicts, planned-alone fallbacks and seam
   hints are lattice decisions that need the whole layer's graph. They move into print
   preparation as one unit and cannot be split from the lattice.
3. **Run identity across the stack.** No object today names "the same wall run" across
   layers with different transforms and cuts. `_track_map` (wall-relative point maps) is the
   seed, but a stack-level run model is new code.
4. **Junction coherence.** The open problem remains. The stack-aware design makes it
   tractable (morph the reference junction geometry layer to layer), but it is not solved by
   the restructuring alone.
5. **Later lattice findings.** Lattice support problems and non-closing routes surface at
   print preparation, not during editing. This needs a clear "prepare / findings" UI and the
   alternative-local-solution search, so an architectural design is not invalidated
   needlessly.
6. **Legacy `LatticeInstance` infills** run before the network stage and need a special
   case.

## Staged migration (testable milestones)

| | Milestone | Test |
|---|---|---|
| **M0** | Expose the envelope: `PrintLayer` early return after `_apply_networks` (no Wall Systems / lattice) → `DesignLibrary.envelope(design, transforms, cuts)` → `DesignerSource.envelope` (rings + per-edge source tags + openings / junctions / centrelines). No caller changes. | Envelope rings == the rings inside the full build, for every layer of the reference network and the benchmark building; envelope cost benchmark. |
| **M1** | Assembly resolve on envelopes: preview + footprints + groups from envelopes; the new envelope support check; full variant builds become a BACKGROUND print-preparation job (cancellable, existing caches). | Plan stream / toolpaths byte-identical to today; exact edit latency (`bench_browser.js`) ≈ seconds instead of 12–44 s. |
| **M2** | Bead-level support in print preparation / Simulation (existing `support.py` on prepared geometry); findings link back to Assembly locations. | The same findings as today's report, in their new place. |
| **M3** | Stack-aware lattice plan per wall run (union of openings / cuts / ends), replacing station ops and `_station_reference`; run side by side with the old path. | Every route closed; bead-level findings ≤ current; an opening edit no longer rebuilds the other layers. |
| **M4** | Wall-relative junction morphing + shrinking-junction motif; alternative local lattice solutions before reporting. | Reference-stack findings well below today's ~51 (zigzag) / 71 (wave). |
| **M5** | Retire the replaced code. | Full suites green. |

Code to retire after M3–M5:
- station cut ops and `_station_reference` probes;
- per-variant scaffold verdict and uncut-reference passes;
- `_cut_join` fallback re-plans;
- variant prefetch inside the Assembly resolve;
- bead-level support during editing.

## Comparison

| | Current | Proposed (after M1 / after M4) |
|---|---|---|
| Editing responsiveness | 12–44 s exact edit | ~1–4 s estimated (envelopes 43 ms / layer + envelope support) |
| Computational complexity | full Designer resolve ×~3 per changed layer per edit | envelopes per edit; lattice once per stack per preparation |
| Implementation difficulty | — | M0–M2 moderate; M3–M4 hard (new generator logic) |
| Lattice quality | per-variant repairs, remote re-plans | solved with every opening known (M3+) |
| Cross-layer coherence | the main open problem | addressed directly (M3–M4) |
| Maintenance | many repair / fallback paths | fewer, after retirement |
| Project compatibility | — | unchanged (authoring state only) |

## Recommendation

**First milestone: M0** (expose and verify the envelope stage). It is small, changes no
behaviour, and turns the measured 43 ms / layer into a supported interface with an equality
test against the full build.

**Then M1**, which delivers most of the editing speed-up while every toolpath stays
byte-identical. Defer M3–M4 until M1 is reviewed manually.

## Implementation status (M0 + M1, 2026-10-11; uncommitted)

- **M0 — envelope exposed (no second geometry algorithm).** `model.PrintLayer.envelope_only` stops the
  Designer's own `_build_effective` right after the wall networks (before Wall Systems / lattice /
  infill / routing); `layer_design.DesignLibrary.envelope(design, transforms, cuts)` resolves the
  document exactly as `build()` does (cuts → semantic transform) and returns
  `design_proto/envelope.Envelope`: material rings (+ per-vertex source attribution by the Designer's
  rule), face beads that bound no ring (plain / Hollow walls without infill: they ARE what prints) and
  other bare strands, processed centrelines, effective walls (thickness), Wall System assignments +
  parameters, openings (+ status), trims, junctions, the cut / transform report. Lattice-only cut ops
  (`station`, `transition`) are dropped from the envelope and its key. `BuiltLayer.envelope_rings` =
  the rings a full build filled — tested EQUAL to the envelope's (reference network with groups,
  mixed benchmark incl. openings / arrays / plane / stations, plane + opening fixture of four Wall
  System families). `DesignerSource.envelope` / `envelope_key` / `prefetch_plain`.
- **M1 — Assembly editing on envelopes.** The page sends `envelope: true`; `/resolve` + `/geometry`
  then use their own adapter + lock (`web._ENV_SOURCES`, `_ENV_LOCK`; canonical builds shared through
  `_CANON`), never wait for / pre-empt print preparation; placements unchanged (footprints still from
  the canonical printed designs); support = the PRELIMINARY envelope-level check
  (`support.analyse_support(level='envelope')`, `_envelope_pair`). Print preparation = the Print
  Simulation's explicit **Generate Print Paths** (the unchanged `/print_plan` pipeline + the
  bead-level support report); outdated paths are labelled, never silently shown as current.
- **Measured** (84-layer benchmark): see `layer_assembly/PROJECT_MEMORY.md` → "DEFERRED PRINT PATHS".
- **Print geometry:** byte-identical to the pre-change pipeline on the benchmark (variants, profiles,
  bead-level support, plan stream, summary — also after envelope editing first).

## Next milestone done: Wall Groups × Wall Systems workspace (2026-10-11; uncommitted)

- The Designer's Wall Systems are split: **Wall Groups** (members, thickness, alignment — the
  architecture, per Layer Design, `doc['wall_groups']`) and the project's **Wall Systems**
  (construction type, parameters, web; assigned to walls or whole groups; `project['wall_systems']`).
  `design_proto/wall_groups.py` composes them into the unchanged build format, so every generator,
  the lineage lattice and routing run exactly as before (migrated projects byte-identical).
- Designer = envelopes only (the full preview stays available as a development flag).
- New **Wall Systems** workspace between Assembly and Print Simulation: construction editing,
  explicit **Generate Wall Systems** (the full pipeline — what the Simulation's "Generate Print
  Paths" did in M1), status NOT GENERATED / GENERATING / CURRENT / OUTDATED, layer inspection.
- The Print Simulation's button is now **Plan Print**: routing / lifts / timing from the CURRENT
  generation only (the server refuses otherwise: `require_generated`).
- Placement is still from the canonical printed footprints (now composed with the project's Wall
  Systems): a construction change can move the pivot of scaling transforms by up to ~0.9 in on
  generated constructions (measured) — kept for exact equivalence; an architectural pivot (envelope
  bbox) is the open decision.

## M2 done (2026-10-11; uncommitted)

Final bead-level support is reported with the generation (Wall Systems workspace), classified
(architectural / lattice / construction, continuity from the Wall System reports), navigable (click
→ layer, centred, highlighted) and kept distinct from the Assembly's preliminary architectural
findings. Thresholds unchanged, nothing suppressed. Next: M3 (stack-aware lattice per wall run).

## Opening validation is architectural (2026-10-11)

The Assembly validates openings and places layers from the architecture only (envelopes, architectural
pier / overhang rules); construction minimums are checked after generation in the Wall Systems
inspection. This closes the last backwards dependency of the Assembly on Wall Systems found in manual
testing. M3 not started.

## M3 done (2026-10-11; manually tested)

Phased Wall Systems (Interleaved, Linked, Chained Loop) are drawn from one stack reference per design
family (root architecture without Designer openings, then the design's own), mapped wall-relatively onto
every layer (partial cover where a band reaches further; chain loops continued). Every run reports its
generator; the previous per-layer generator remains (`WALL_LATTICE_MODE=layer`). Not yet on the stack
reference: Skin + Web (lineage scaffold + station ops, so `_station_reference` stays), transform-group
layers, Chained Loop rings / cross-Z transitions, junction territories. Those are M4; code retirement is M5.
Benchmark: coherence improved where sections / openings changed the fit, findings unchanged (53),
generation +1.9 % time, +1 % memory.

## M4 — Round-junction orphan fillet closure (2026-10-12; junction-coherence sub-pass; UNCOMMITTED)

**Reported junction issues (manual testing): awkward / dense geometry, disconnected or poorly
integrated wall paths, inconsistent rounded vs square transitions, single-bead walls that
connect badly to thick Wall Systems, parallel lanes that fail to continue around connected walls,
occasional gaps.** Reproduced on the reference network with every construction × junction style
× Line 1 variant: under `round` junctions and a non-member attached wall (`inherit` network-wall
thickness, or `single_bead` with explicit `wall.thickness=0`), every Parallel / Interleaved /
Linked / Chained Loop scenario produced OPEN ROUTES at Line 1's attachments. Root cause: the
junction fillet built by `network.round_junctions` lands on the host ring's FACE polyline;
`_apply_wall_systems` later drops that face bead and replaces it with Wall System lanes a bead
inside the face, leaving the fillet's ring-side endpoint in space. `miter` junctions do not
produce fillets (no corner-trim), hiding the bug. Attached-wall orphan ends are only visible for
non-members because `_connect_skins` runs only on the mixed-region path. Fix (central, construction-
agnostic, `design_proto/model.py` `_apply_wall_systems` final pass):

1. Collect orphan fillet endpoints by `(host source, partner wall)`.
2. For each pair of orphans at ONE attachment, pick a SHARED attach point and connect BOTH
   orphans to it with a `junction_step` connector.
3. Shared point = nearest Wall System lane point within 1.5 × wall thickness (step-in);
   otherwise the pair's own midpoint (face-chord recovery for Parallel rectangles whose lane
   does not reach the attachment).
4. Net odd parity introduced: 0 (lane cycle picks up two edges at ONE degree-4 node).

Reports `network['junction_steps']`. Preserves the M3 stack reference and every generator
attribution. 1724 design_proto, 92 toolpath_proto, 681 layer_assembly tests green; 30 new
regressions in `design_proto/tests/test_junction_coherence.py`. 84-layer bench: findings
byte-for-byte identical (53 / 53 / 51 / 51 / 51 / 48 / 61 / 45), wall-clock 407.5 s (within
variance), peak RSS 1967 MB.

**Still open (future junction sub-passes):** Parallel-rectangle lane dropouts near attachments
(the lane itself is clipped out by the host's own junctions — the step fallback routes AROUND
this hole using the face chord but does not restore the lane); exterior-wall priority for
single-bead to thick-wall transitions (new user decision #1 — step-in attaches to NEAREST
lane, not necessarily the outermost); shrinking-junction motif and alternative local lattice
solutions (both need design input before any code).

## M4 — Chained Loop rings and cross-Z transitions on the stack reference (2026-10-12; first sub-pass)

**Scope.** Two of the Chained Loop cases M3 explicitly left on the per-layer generator now draw
from the stack reference:

- **Closed rings.** The guard in `_chain_path_ref` that returned `(None, None)` for every
  `st.run.cycle` run has been removed. A ring layer whose Chained Loop lies on the ring / stack
  reference now uses the existing ring-reference branch (continuous u on the reference's loop
  grid), closes the output explicitly (`out[-1] = out[0]`), and reports `closed = True`. The
  reference's even loop count propagates, so no new parity check is needed. If the mapped strict
  samples do not cover one full turn of the layer ring (within 10 % of `st.L`), the function
  bails and the own fit draws the ring with the new reason "a closed Chained Loop ring could not
  close on the stack reference (parity / coverage mismatch); fitted whole on its own loop count".
- **Cross-Z transitions.** `generate` no longer forces `ref = None` when `chain_vc` is non-empty
  on a stack reference. The transition deformation (the `adj` closure of `_chain_path`, factored
  out as module-level `_chain_adj`) is composed on top of the mapped reference curve inside
  `_chain_path_ref` using `side = -sign * tau`. The adjusted offset is validated inside the strip
  (`abs(y_adj) ≤ H`); if it would leave the wall (natural inputs never do — this is a defensive
  guard), the function bails and the own fit draws the run with "a cross-Z transition next to an
  Assembly opening pushed a chain loop past the wall face on the stack reference; the chain is
  pre-deformed by its own fit instead".

**Fall-back.** Both new cases return `(None, None)` from `_chain_path_ref` on failure; `generate`
runs `_chain_path(..., chain_vc, junction_ends)` exactly as before, so the previous output and
every M3 invariant are preserved on the fall-back branch.

**Attribution.** The new cases enter the existing `generator / generator_reason` reporting:
rings and transition runs now show up under `generators={'stack': N}` and inspection surfaces
them in `/api/wallsystems/generate → inspection.generators` alongside M3's phased bands. The
former "fitted whole (identical ring geometry gives the identical chain)" reason text is replaced
by "could not close on the ... reference" and only appears on genuine fallback.
`WALL_LATTICE_MODE=layer` continues to force the previous generator for every band.

**Measured on this laptop (84-layer mixed benchmark, 8 workers, cold):** bead-level findings
unchanged across all 8 edit scenarios (53, 53, 51, 51, 51, 48, 61, 45 — byte-for-byte). Print plan
item count unchanged (6534). Wall-clock total 425.6 s → 409.1 s (−3.9 %, within bench variance).
Peak RSS 1973 MB → 1974 MB. Print geometry byte-identical to the pre-M4 output on the capped-chain
path (the own fit still runs through the factored `_chain_adj`, verified by `test_wall_lattice_mode_layer_matches_pre_m4_output`).

**Still not on the stack reference (future M4 sub-passes).** Skin + Web / Adaptive Truss
(lineage scaffold + `_station_reference`); transform-group (semantic) layers; junction-territory
morphing (the Chained Loop link's 1.53 in worst-layer coherence in the M3 benchmark is
attributed to this — not to rings or transitions); shrinking-junction motif and alternative
local lattice solutions (the two M4 items that need design input before any code).

## J2 — Shared junction geometry (2026-10-12; FIRST PASS + MAINTENANCE; UNCOMMITTED)

**First pass (detect-and-fallback).** Fixes the J1-reported "circle grazing rectangle, Parallel
Walls, no coverage of the circle region" bug by letting a multi-wall same-system region fall
back to a UNITED generation when the per-wall split yields zero runs for any part. Hollow +
Parallel scope only (Skin + Web uses the mixed-region split path). Parallel adds a lane
stitcher `wall_systems._join_parallel_at_junctions` that pairs open lane endpoints across
strips at junction nodes (closest-pair, ~10 bead tolerance). The united path writes
`j2_united_region: True` and the sources set on the Wall Systems report; the split path and
every M3 fallback are preserved (overlapping-X yield test continues to pass). A dormant
`JunctionRegion` + `Arm` + `extract()` module (`design_proto/junctions.py`) names the agreed
vocabulary for the next J-pass but is not yet consumed by any generator — the actual J2 fix
runs on material rings via `_apply_wall_systems`.

**Maintenance pass (follow-up, same day).** The J2 probe loop ran `WS.generate` per part twice:
once as a probe to decide fallback, once again in the split-per-part loop. The maintenance pass
reuses the probe output in the no-fallback branch when every part's `chord_tips` list is empty
(same arguments → same output), halving generation for Hollow / Parallel multi-wall same-system
regions whose probes already succeed. M4 step-in also reuses the existing `kept_pts` cache for
orphan fillet + Wall System lane samples. `_seam_hints()` + `_virtual_faces()` cached per
`_apply_wall_systems` call. All geometry byte-identical to the pre-maintenance baseline.
`checks.no_protrusions` dropped from `run_all` (was a pure alias of `envelope_containment`;
kept callable). Timing watchdog recalibrated per-fixture (circle_rect 3.0 s, corner_90 2.0 s,
cross 1.5 s, others 0.9 s) so hardware variance no longer false-alarms a ~2 × regression
watchdog.

**Measured.** Reference-network `parallel round explicit10` (4 members, probes succeed):
1.24 s → 0.78 s (−37 %), `WS.generate` call count 8 → 4. `circle_rect` (fallback fires):
unchanged at ~0.37 s build-only (3 `WS.generate` calls — probe × 2 + united). `parallel round
inherit` (mixed region, probe branch not entered): unchanged. 84-layer mixed benchmark not
re-run this pass (byte-identical geometry at the single-layer level is a stronger pin — a
build-level perf delta would be ≤ the above).

**Still open (future J-passes).** Parallel-rectangle lane dropouts near attachments (the lane
itself is clipped out of its own strip by the host's junctions — the J2 fallback routes AROUND
this hole via the face chord but does not restore the lane). Single-bead → exterior-skin
priority (step-in attaches to the NEAREST lane, not necessarily the outermost one). Shrinking-
junction motif and alternative local lattice solutions (both need design input before any
code; also listed for the next M4 sub-pass).

## J3 — Shared junction architecture integration (2026-10-12; INTEGRATION DONE; UNCOMMITTED)

**Scope.** Make `JunctionRegion` the authoritative representation of corners and junctions for
Hollow Skin and Parallel Walls. Architectural integration, NOT a new motif pass. Byte-identical
generated geometry; the records flow through the pipeline and are genuinely consumed.

**Extraction (`design_proto/model.py` `_apply_networks`).** After each `N.resolve(...)` +
`N.junction_corners(...)` and BEFORE `N.round_junctions(...)` (which reshapes the bead chains
onto fillet points so chain endpoints no longer land on arrangement nodes), the pass calls
`junctions.extract(res, source_walls=self._source_walls, junction_style, junction_radius)`.
Records accumulate on `self._junction_regions` and are also serialised into
`summary['junction_regions']` → `meta['network']['junction_regions']` (each entry has `key`,
`kind`, `source_ids`, `style`, `center`, `arm_count`, `arms[]`).

**Threading (`design_proto/model.py` `_apply_wall_systems`).** Reads `self._junction_regions`
once at method entry, passes it as a `junction_regions=` kwarg to every `WS.generate` call: the
one-wall fast path, the J2 probe loop, the J2 united fallback, the split-per-part loop and the
motif-regen fallback.

**Generator signature (`design_proto/wall_systems.py` `generate`).** Added trailing kwarg
`junction_regions=None`. Default preserves pre-J3 behaviour (every existing caller / test /
tool works unchanged). The function reads it only on `type in {'hollow', 'parallel'}`.

**Parallel consumption.** `_join_parallel_at_junctions(polys, bead, junction_regions=None)`
tags every open endpoint with the nearest region centre within ~3 × bead; in the closest-pair
stitch loop, ties on distance are broken by preferring a tagged pair (strictly closer untagged
pair still wins). Return signature: `(polys, stitch_info)`. `stitch_info = {by_region, by_distance}`
reported on the Wall System's rep as `parallel_stitching`.

**Hollow consumption.** Hollow / Skins Only does NOT route through `wall_systems.generate` —
its skin beads come from the base ring-offset pipeline inside `_apply_networks`. The shared
representation reaches Hollow INDIRECTLY: the pre-processed material rings already encode
miter / fillet corner geometry (`round_junctions`), and `meta['network']['junction_regions']`
is available for any future Hollow-specific junction treatment to consume without re-
extracting.

**Diagnostic.** `rep['junction_regions_seen']` lists the keys of regions whose centre lies
inside the generator's rings — a direct consumption-proof signal (removing the J3 extraction
makes this field empty; a regression test catches that).

**Verified.** 10 new consumption-proof tests in `design_proto/tests/test_j3_integration.py`
pin: serialisation shape, arm-count agreement, source-wall classification on `is_single_bead`,
Parallel's `junction_regions_seen` on tee + parallel + round, Hollow's indirect-availability
pin, stitcher decision breakdown, pre-J3 behaviour preserved when regions=None, region-tagged
tie-break fires, kwarg backward compatibility, no state leak across replays. Full suites:
design_proto 1784 (+10 J3), layer_assembly 681 (unchanged), toolpath_proto 92 (unchanged).
Geometry byte-identical on 7 harness fixtures + 24-cell reference-network sweep (SHA-1 of
`(role|src|points…)` identical, path counts identical, junction_steps / j2_united_region /
per_run unchanged). Perf: +5–15 % on scenarios with regions (one `extract()` call per
resolved component + per-endpoint distance checks); `WS.generate` call count unchanged.

**Covered this pass.** Parallel Walls (direct consumption via `_join_parallel_at_junctions` +
`junction_regions_seen`). Hollow / Skins Only (indirect via `meta` and the pre-processed
rings). Interleaved / Linked / Chain IGNORE regions this pass — their crossings use the M3
stack reference.

**Legacy junction machinery retained unchanged.** `network.junction_corners`,
`network.round_junctions`, the M4 orphan-fillet step-in in `_apply_wall_systems`, J2's probe +
united fallback, Chained Loop corner / junction motifs, `_check_junction_motifs`,
`_contact_staples`. J3 is purely additive; nothing removed.

**Architectural limitations (not J3 scope).**
- `junctions.extract()` filters out same-source corners (`len(srcs) < 2 and len(arms) < 2`),
  so a closed rectangle's four corners emit 0 regions. For `circle_rect` (two overlapping
  sources R + C), extract also returns 0 regions because the bead chains in that fixture
  don't terminate at an arrangement node (the X-crossings are interior; current extract
  reads chain endpoints only). Vocabulary expansion is J4 territory.
- Hollow still uses the base ring-offset pipeline. Rewiring Hollow to a direct
  `WS.generate(..., type='hollow')` branch so it reads regions is a plausible J4 refactor.
- The region tagging in `_join_parallel_at_junctions` uses a 3-bead tolerance; wider / per-
  wall-thickness tolerances could tag more Parallel strip endpoints but risk cross-junction
  mis-tagging. Not changed.

**Next steps toward selectable junction motifs (NOT J4 scope — forecasting).**
1. Widen `junctions.extract()` so bead-level crossings and single-source corners surface.
2. Route Hollow through a direct `WS.generate(..., type='hollow')` branch.
3. Add a junction-motif descriptor on `JunctionRegion`; let generators dispatch on
   `region.motif` (user-selected per junction).
4. Make `_join_parallel_at_junctions` region-DRIVEN once (1) + (2) are in place.

## J4a — Shared geometric regions and connectivity (2026-10-12; DETECTION DONE; UNCOMMITTED)

**Scope.** Extend the J1–J3 junction framework into FIVE conceptual region types, with
reliable detection, stable identities, and diagnostic reporting. Byte-identical geometry;
motif consumption is J4b/J4c scope.

**Region types** (`design_proto/junctions.py`):
- `CornerRegion`      — direction change inside one source (e.g. rectangle corners).
- `JunctionRegion`    — multiple walls connecting (existing J2/J3 record).
- `EndCapRegion`      — wall termination: free end or opening jamb.
- `ConstrainedRegion` — section too small for normal construction (short pier, short jamb).
- `TransitionRegion`  — explicit relationship between a normal region and a constrained one.

Each record carries a stable source-prefixed `key`, so a region keeps its identity across
layers, through semantic transforms and across cuts. Serialised on `meta['network']['shared_regions']`.

**Extraction (`design_proto/model.py` `_apply_networks`).** After the per-component loop,
the architectural extractors (`J.extract_corners`, `J.extract_end_caps`,
`J.extract_constrained_sections`, `J.extract_transitions`) run ONCE per build on `self`
directly — they walk the Designer source paths + wall table + openings + cut transitions
and do NOT need a `NetworkResult`. The pre-existing J3 `self._junction_regions` list is
copied into the new `RegionSet.junctions`.

**Constrained-section rule.** `required_length = max(wall_thickness, 2 × bead)`. Reasons:
`short_section` (whole open path), `short_between_openings` (pier between consecutive
openings on the same source), `short_jamb` (open-path jamb margin to the nearest opening).

**Bug audit — the four reported J4a bugs.**

| # | Reported bug | Current state after audit | J4a response |
|---|---|---|---|
| 1 | Rounded circle/rectangle junctions developing gaps | Already corrected by J2 (united-generation) + Parallel stitching + M4 orphan-fillet step-in. Automated continuity + exterior-continuity + region-coverage all pass for every circle_rect × system × style. | Pinned by `test_j4a_circle_rect_connectivity_across_systems_and_styles`. |
| 2 | 45° chamfers at rectangular corners on certain layers | The ARCHITECTURAL corner always reports 90° miter (`test_j4a_rectangular_corners_architecture_stays_90` for every system). The Chain system's current CORNER MOTIF produces ~8 short (0.5-1.5 in) 45° segments near each rectangle corner — these are internal motif detail, NOT architectural chamfers. | Documented via `test_j4a_chain_corner_motif_segments_are_reported_as_a_known_issue` so a J4b motif revision can show its diff. No J4a geometry change. |
| 3 | Single-bead attachments creating exterior protrusions | Current pipeline closes continuity for every scenario. The branch's two lanes are currently DROPPED when the host is a Wall System (separate rendering issue, not an exterior protrusion). | Pinned by `test_j4a_single_bead_attachment_reports_connected_regions`. Branch preservation under Wall System host → J4b. |
| 4 | Short pier between opening arrays producing disconnected fragments | DETECTED by `ConstrainedRegion` with correct reason + available/required lengths. | Pinned by `test_j4a_short_pier_between_two_openings_is_flagged` and `test_j4a_short_jamb_margin_is_flagged`. The compact-loop motif library for these regions is J4b scope. |

**Tests.** 38 new regressions in `design_proto/tests/test_j4a_regions.py` covering: region
extraction completeness per fixture, `region_type` consistency, rectangle 90° corners under
every system, circle produces no corners, T free ends → end-caps, opening jamb pair, short
pier flagged, wide pier NOT flagged, short jamb flagged, transition per constrained region,
circle/rect connectivity, Chain corner-motif documentation, single-bead attach regions,
region keys stable across rebuilds.

**Perf.** Region extraction = ~0.1 % of build time on harness fixtures (0.01–0.29 ms vs
20–386 ms). Full suites: design_proto 1822, layer_assembly 681, toolpath_proto 92 green.
Geometry byte-identical to the J3 baseline on 7 fixtures + 24-cell reference-network sweep.

**Still open — J4b scope.**
1. Compact-loop motif library for `ConstrainedRegion` scenarios.
2. Chain system corner motif review (option to produce clean miter corners when document
   requests miter).
3. Single-bead attachment preservation under a Wall System host (currently the two lanes
   are dropped).
4. `TransitionRegion` motif — the pattern shape as it morphs from normal to constrained.
5. Begin consumption of `CornerRegion` + `EndCapRegion` in Hollow / Parallel generators
   (motif-aware caps / corners).
6. Fill the `JunctionRegion.arms[].face0`/`face1` connect-points so Parallel's stitcher
   becomes region-driven rather than tie-breaker.

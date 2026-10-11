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

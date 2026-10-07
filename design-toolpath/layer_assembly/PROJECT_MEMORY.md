# Layer Assembly — Project Memory

Sub-memory of Design + Toolpath (see `../PROJECT_MEMORY.md` for the Designer). Kept separate so Z / stacking / 3D-preview work can iterate without reading or retesting Designer internals.

Structure: CURRENT ARCHITECTURE → DESIGNER CONTRACT → CURRENT DECISIONS (by topic) → KNOWN LIMITATIONS → TESTING → NEXT STEPS → HISTORY (superseded approaches, compressed). All of this is uncommitted work of 2026-10-06, audited and cleaned for the checkpoint.

## Purpose

Assemble reusable 2D LAYER DESIGNS (made with the Designer) vertically into a printed form: which design is used at each Z, where every layer sits, and whether every layer is physically supported.

    DESIGNER        — reusable 2D Layer Designs (walls, trims, openings, lattice, beads, routes)
        ↓ LayerSource (designer_source.py — the only Designer import)
    LAYER ASSEMBLY  — sections by physical height → layer instances + Z + transforms;
                      transform groups → semantic variants; vertical support + headers
        ↓ future
    PRINT / MACHINE OUTPUT (Z, G-code, Pi) — not started

## Current architecture

**Owns:** layer ordering, physical Z, ONE global layer height, section and assembly-wide transforms, transform-GROUP choices (which source walls follow which centre), vertical support analysis, headers / assembly objects, Assembly undo / redo. **Never knows** what a wall, lattice, junction or opening is.

Package `design-toolpath/layer_assembly/`:
- `model.py` — `Section(design_id, height, label, transform)`, `Assembly(layer_height, sections, name, transform, objects, overrides, center_mode, centers, max_overhang)`, `TransformCenter(id, x, y, sources, label)`; `resolve()` → `ResolvedAssembly(sections, instances, warnings)`. `LayerInstance(index, design_id, section_index, z_bottom, z_top, transform, parts, semantic)`; `geometry_key` / `transforms()` name its semantic variant. `relative_transforms` (R_g = T_whole⁻¹ ∘ T_g, identity within 1e-9 / 1e-7).
- `source.py` — the `LayerSource` protocol and `StaticSource` (in-memory TEST DOUBLE; with `transforms` it moves tagged vertices — only to exercise the Assembly without the Designer, never the real mechanism); `stack_geometry` (tests / tools).
- `designer_source.py` — the adapter to `design_proto/layer_design.DesignLibrary` (see contract).
- `groups.py` — `build_groups` → `GroupModel` (group_of {source → centre}, per-design vertex group tags (to SEE groups), footprints, auto centres, warnings, suggestions).
- `components.py` — detects connected printed masses; only used to SUGGEST groups.
- `support.py` — vertical support (minimum layer overlap, bridge vs overlap classification, headers, overrides, aggregation).
- `objects.py` — `Header`, `SupportOverride` (KINDS registry).
- `preview.py` — `build_stack` (each instance + Z + its geometry, fetched once per design / variant); `isometric_svg` (prototype check).
- `web.py` — Flask blueprint `/api/assembly/resolve`, `/api/assembly/geometry` (stateless; the page holds the project).
- `bench.py` (performance benchmark), `demo.py` (prototype CLI) — tools, not app code.
- UI: `design_proto/static/assembly.js` (+ markup in `index.html`); talks to the Designer only through `window.Designer` and to the backend only through `/api/assembly/*`.

**UI (five columns, no tabs; each control column scrolls on its own; below 1280 px the control columns wrap above the preview):**
LAYER DESIGNS (190 px: designs, derived-design creation, rename, Edit in Designer) | LAYERS (230 px: Undo / Redo, layer height, totals, ordered sections with height + section transform, reorder / delete) | ASSEMBLY TRANSFORM (240 px: assembly-wide transform, scale centre mode, transform groups, Assign geometry, Walls → group table, Suggest from components, regenerated connectors, derived junctions) | STRUCTURE (230 px: Bead width, Maximum overhang, derived Minimum overlap, aggregated findings, headers, Ignore / restore) | 3D PREVIEW (flex).

## Designer ↔ Assembly contract

- **Assembly asks:** `geometry(design_id)` (canonical) or `geometry(design_id, transforms={source id: (k, tx, ty)})` — a SEMANTIC VARIANT: the layer's group placements relative to its own placement, applied to SOURCE PATHS by the Designer. There are no cross-Z hints in the contract: the Designer tracks a variant's lattice against the untransformed design itself.
- **Designer returns** `LayerGeometry(design_id, polylines, bead_width, diagnostics, sources)`: printable polylines each with `src` (per-vertex source path id — source_attribution.py), the project bead width, diagnostics {lattice (incl. lineage), trims, semantic {connectors, moved, junctions}, timing}, and source labels.
- **Caching:** `web._SOURCES` (LRU 4) keyed by the full designs JSON → one `DesignerSource` per design set; inside it canonical geometry (`_plain`) and variants (`_variants`, LRU 512, key = design + `semantic_transform.spec_key`). The Designer library caches builds (`_cache`, `_tbuilds`) and content-keyed scaffolds / documents (`layer_design._SHARED`, `_TRACK_MAPS`, `semantic_transform._WALLS`, `_RELATIONS`). Variants are prefetched in spawned worker processes (`LAYER_ASSEMBLY_WORKERS`, default min(8, cores); 0 = in process; falls back in process).
- **Needs a fresh Designer resolve:** any designs change (geometry, openings, lattice, material — bead width included) → new DesignerSource, every design and variant rebuilt; a transform change that produces a NEW source-transform spec (only those variants).
- **Needs NO Designer resolve:** layer height without group transform change in footprint mode, Maximum overhang, headers, overrides, renames (names are outside the cache key), section reorder that keeps specs.
- **Placement:** every vertex of a layer is placed by the layer's own `InstanceTransform`; a vertex's group gives only its local FRAME (finding / header coordinates). The Assembly never moves resolved beads per vertex (the abandoned rubber-sheet approach — see History).

## Current decisions

### Stack resolution
- Sections are given by desired PHYSICAL height; layer counts are derived by rounding section BOUNDARIES (cumulative height / layer height, half up): every transition within ½ layer of its Z, no accumulation; a section rounding to 0 layers is warned. Changing the layer height re-resolves the same intent.
- Instances only REFERENCE a design (Base used for 40 layers exists once).

### Transforms
- `SectionTransform(profile, shift=(dx, dy), scale_in)`, amounts PER LAYER, inputs step 1/16 in (stored as typed). Per layer: scale about the CURRENT footprint centre, then translate (physical inches, never scaled). Each section continues from the previous section's END state (position continuous; slope not matched — known).
- Profiles: linear m = k; quadratic m = k(k+1)/(2n) — v is the FINAL per-layer rate (a plain v·k² was rejected as unintuitive).
- SCALE is a uniform scale (relationships preserved; bead spacing / thickness scale with the WHOLE layer). A true constant-thickness inset would be a Designer re-resolve behind the LayerSource boundary (not built).
- Assembly-wide transform: the same model over the whole stack (n = all layers), composed geometry → assembly-wide → section → placement, both shifts physical, scales multiply; identity = section-only behaviour exactly.
- Result: ONE `InstanceTransform` per layer, consumed identically by preview, support, headers and (future) machine output.

### Transform groups (semantic)
- `center_mode`: 'footprint' (default, one pivot) | 'multiple'. A `TransformCenter` moves EXACTLY its explicitly assigned source walls (`sources`, the Designer source path ids — stable across derived designs and openings). First listing wins (warned); unassigned walls follow the whole footprint; moving a pivot never changes membership; no nearest-centre / Voronoi rule anywhere. Components only suggest.
- **Principle (durable): transform the architectural relationships, then resolve.** Per layer the Assembly computes each group's placement relative to the layer's own and asks the Designer for the variant. Walls keep ONE physical thickness (only the whole layer's placement scales it); connecting walls are regenerated by the Designer, never stretched.
- Explicit connectors (an authored wall attached to moved forms) are regenerated between their transformed attachments; DERIVED junctions (forms that merely intersect) are recomputed from the transformed forms and disappear when they separate — both reported per variant (`connectors`, `junctions`) and listed in the Assembly Transform column.
- Identity is exact: near-identity placements request nothing, the layer prints the canonical design.
- Colours are diagnostic in Multiple mode: assigned geometry = its group colour, UNASSIGNED = the Designer blue `#4a9eff` (no blue in the group palette).

### Vertical support
- **Rule: mud requires physical support below** — from the layer directly below or an explicit assembly object. No door special case.
- **Input: Maximum overhang h** (`Assembly.max_overhang`, default 1.5 in, ≥ 0). Required overlap per transition o = w_u − h (h clamped to [0, w_u], warned); the report carries the derived `min_overlap`. The bead width is the Designer's project material (Base's `material.bead_width`), never Assembly state.
- **Math:** parallel beads w_u, w_l offset d overlap min(w_u, w_l, (w_u + w_l)/2 − d). Quick accept within the allowed centreline offset; otherwise the EXACT covered length of the upper bead's cross-section by the union of lower capsules (both legs measured at a vertex). Sampling every ½ bead; regions within 4 beads merge; < 2 beads is noise.
- **Classification:** HEADER NEEDED (`bridge`) when along an unsupported run ≥ 1 bead lies over an EMPTY CORRIDOR (w_u + 2·w_l across the strand touches no lower bead) or nothing lies within 2·w_u; otherwise INSUFFICIENT LAYER SUPPORT (`overlap`, required vs worst overlap). Geometry only.
- **Aggregation:** `SupportReport.groups()` — one group per kind / status / section / place (contiguous or scattered layers); one card and one viewport label per group; headers stay individual. Ignore records a whole group.
- **Headers** (`objects.Header`): anchored at a layer boundary in that layer's local coordinates (follows transforms); length = span·scale + 2·bearing; snaps to the detected span; top at the boundary, hanging down `thickness`; pockets REPORTED, not cut. Created only from a warning. Overrides (section, local layer, centre) mark a finding `overridden`, never supported.
- **The support checker is a test of the geometry: never weaken it to hide findings.**

### Undo / redo
- Separate from the Designer's (`asm.hist`, 200 steps): snapshots of the Assembly state + header counter + design names + bead width; edits of one field within 1.5 s merge; a drag is one step.

## Known limitations

- **Transformed junction lattice (the main open problem):** on the reference stack (`design_proto/tests/reference_network.py`, 32 layers, Circle 1 / Rect 1 / Rect 2 in three groups scaling, round junctions) support reports ≈ 51 (zigzag) / 71 (wave) findings, ~85 % on lattice at the Rect 1 ↔ Circle 1 lens and the Rect 1 ↔ Rect 2 crossing. Every variant's route closes. Cause (Designer side): junction transitions are re-planned per layer. Precise statement and next step: Designer memory → "Transformed-junction lattice".
- Cost: each grouped layer with a new spec is a full Designer resolve (~0.9 s CPU, ~0.12 s wall with 8 workers); support analysis ~21–25 ms per layer and dominates warm edits.
- Only the layer directly below counts (no corbelling / multi-layer bridging); geometric, not structural.
- Headers / overrides keyed by section index (reorder can orphan them; reported); no header self-support check; footprints are bbox squares.
- Attachments are coincidence in the untransformed design (no persistent constraints, Designer side).
- No persistence across page reloads.

## Testing

- Pure (no Designer, fast): `test_model.py`, `test_preview.py`, `test_transforms.py`, `test_assembly_transform.py`, `test_support.py`, `test_overlap.py`, `test_bridge_classification.py`, `test_components.py`.
- Real Designer (contract / integration): `test_designer_contract.py`, `test_support_contract.py`, `test_semantic_groups.py`, `test_web.py`, `test_reference_network_stack.py` (the PRIMARY realistic integration fixture), `test_assembly_ui.py` (+ `tests/js/ui_assembly_smoke.js`, real app.js + assembly.js under node against the real backend).
- Commands and tiers: see the Designer memory → "Testing workflow".

## Next steps

1. Transformed-junction lattice coherence (Designer side; see the Designer memory).
2. Cheaper transformed layers: reuse stable topology instead of a full plan per variant; one shared tracking reference per worker pool (each worker currently rebuilds the untransformed reference scaffold).
3. Vectorise / grid support analysis (warm-edit cost).
4. Persistence of a project file; deleting designs in use; stable section ids for headers / overrides.
5. Slope-inheriting profiles; per-inch amounts; multi-layer corbelling; header pockets as Designer variants; preview improvements; Z / machine output.

## History (superseded approaches, compressed — details in git history)

- **Per-component scale centres** (connected component = transform centre) → rejected: a circle room and a rectangle room joined by walls are one mass but need two centres. **Manual centres without membership** → useless. **Component-key `group` membership** → replaced by explicit SOURCE-WALL membership (no persisted projects existed, so no compatibility kept).
- **Per-vertex placement of resolved beads by group** (with a `split` for segments joining two groups) → abandoned the same day: the connecting wall stretched / tore. Replaced by SEMANTIC transform groups (Designer re-resolves). Do not return to rubber-sheet transformation of finished printable geometry.
- **Support threshold:** ½ bead → explicit Minimum layer overlap → Maximum overhang as the input (manual testing: designers think "bead 3, overhang 1"). The legacy `min_overlap` input was removed in the checkpoint audit (never persisted). Bridge classification "nothing within 2·w_u" → empty-corridor rule (lattice / caps beside a small opening made it an overlap problem).
- **Layout:** one column → Layers / Structure tabs → four columns → five columns (Assembly Transform became a design system of its own). Tab code removed.
- **Cross-Z lattice:** per-layer independent planning (stitch counts oscillated 36 → 34 → 36; 12 of 31 transitions unsupported) → station tracking against the untransformed design → partial tracking with parity repair (Designer memory).
- Unassigned geometry: grey → design colour → Designer blue (design colours read as groups).
- Benchmark history (cold / warm, 8 workers): 49 layers 8.2 / 1.1 s, 99 → 12.7 / 2.2 s, 299 → 38.3 / 6.3 s before stabilization pass 2; current numbers in the Designer memory → "Performance".

## Last Updated

2026-10-06 — checkpoint audit: memory restructured (current first, history compressed); legacy `min_overlap` input and the per-vertex placement helper removed; contract documented. Earlier the same day: subsystem, workspace, transforms, headers / support, undo / redo, transform groups → semantic variants, minimum-overlap rule → maximum overhang, aggregated diagnostics, five columns, project bead width, reference-network stack regression. Uncommitted, for manual review.

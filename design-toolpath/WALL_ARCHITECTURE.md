# Wall / Junction / Construction Architecture — FINAL

**Status:** APPROVED (revision 4, 2026-10-15). **M0–M4 implemented 2026-10-15, manually reviewed (with the correction pass) and checkpointed (`b4ccd16`); golden re-baselined (full hashes) at the checkpoint. M5 implemented 2026-10-15, manually reviewed and checkpointed; M6 / M7 in progress (2026-10-16).** Revision 4 records the user's FINAL decisions (§F), which supersede the corresponding parts of §5, §6, §9 and §12.

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
- Today's mixed-system junction already SPLICES an out-and-back branch into ONE host path (`wall_systems.connect_branch`, `:1836`):
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
- Older readers (schema 1–6 migrations, legacy `wall_systems` documents, `path.wall`, `network_walls`) stay as one-way migration code.

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
  - **Storage choice:** PROJECT-level in shadow mode, so no document, payload or `.mudjob` byte changes. M5 moves the records into the lineage-owned documents (§4).
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

### Before M5 — to decide

1. *(M5: no longer arises in any golden case — those nodes were the unresolved trims' overlap; still open for genuine band overlaps without trims.)* **Band-overlap contacts** (production_only above): with source-line junctions, bands that overlap without their source lines touching are NOT a junction. Should M5 treat them as plain material union (no junction, no corner treatment), or keep a junction there?
2. **Per-corner shapes:** §4 has one `shape` per junction; legacy overrides are per corner (no real project uses any). Is one shape per junction enough? M4 keeps the per-corner list.
3. **Record storage:** move the records into the lineage-owned documents (as §4) — confirm. *(M5 did not need it: trims resolve by the identity rule and record the ids; records stay project-level.)*
4. ~~Re-baseline full golden hashes~~ — DONE at the M0–M4 checkpoint after the user's review. Before it, every strict difference was a full hash only; the label-stripped `geom` hashes and `.mudjob` were equal. `GOLDEN_STRICT=1` now passes 23/23.
5. **Junctions that APPEAR only on transformed layers** stay unnamed (per-layer topology, §F1) — confirm that no record or treatment is wanted for them.


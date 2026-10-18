# Wall / Junction / Construction Architecture — FINAL

**Status:** APPROVED (revision 4, 2026-10-15). **M0–M4 implemented 2026-10-15, manually reviewed (with the correction pass) and checkpointed (`b4ccd16`); golden re-baselined (full hashes) at the checkpoint. M5 implemented 2026-10-15, manually reviewed and checkpointed (`dce9366`). M6 + M7 (with the through-wall / Chained Loop correction pass) implemented 2026-10-16, manually reviewed and checkpointed; M8 / M9 and full-wall junction work not started.** Revision 4 records the user's FINAL decisions (§F), which supersede the corresponding parts of §5, §6, §9 and §12. **Revision 6 (2026-10-18, restart) adds §Z: the binding Construction-through-Z PRODUCT SPECIFICATION, and the physical-width / material / Skin + Web product decisions, brought forward as product knowledge from the archived later work. Code baseline = the M6 + M7 checkpoint `8988108`; nothing in §Z is implemented in it unless §Z says so.**

## §Z. CONSTRUCTION THROUGH Z: PRODUCT SPECIFICATION (binding; brought forward 2026-10-18)

> **Read first: status of this section (2026-10-18).**
> 1. **Code baseline:** the manually reviewed 2026-10-16 checkpoint `8988108` (M6 + M7), branch `restart/oct16-base`. It is the current implementation. Everything the code does is described by the M0–M7 notes below and the memories, not by §Z.
> 2. **Plan going forward:** the product specification in this section (§Z.0 hierarchy, §Z.1 invariants, §Z.2 physical / material decisions, §Z.3 Skin + Web single-layer decisions). It was written 2026-10-17 / 18 during later work and is kept as PRODUCT KNOWLEDGE.
> 3. **Archived, NOT current architecture:** every later implementation. That includes the post-`8988108` commits on the old line (`a11fcc1` cleanup, `76ebb87`, `1610f95` test tiers, `931d4b8` physical width, `bfce435` material controls, `e3e93dd` Skin + Web Pass A, `116513e`–`8997650` Z0–Z2) and the failed post-Z2 experiments (`bfb0e93`, "archive: experimental construction-through-Z work before rollback"; local branch `archive/construction-through-z-2026-10-18`, also the tip of `restart/post-print-simulation`). Mine them for ideas and measurements only (§Z.6). Their mechanisms are not the plan.
> 4. **How work proceeds:** in SMALL, manually reviewed milestones, each checkpointed on its own before the next starts. No milestone is scheduled yet (§Z.7).

### Z.0 Binding product hierarchy (user, 2026-10-18)

These are PRODUCT REQUIREMENTS for every Wall System (Skin + Web webs, Interleaved / Linked Waves, Chained Loop, Parallel where applicable, future systems). Rules 1 and 2 are VALIDITY constraints: a construction that violates either is invalid, whatever else it achieves. Rules 3–7 rank VALID solutions, in order.

1. **Physical support is a hard validity constraint.** Every printed construction layer is supported by the construction below within the project's Maximum Overhang, except geometry explicitly carried by a valid bridge / header / other supported-span mechanism. A construction exceeding the allowed overhang is INVALID. Support findings are never hidden, filtered or weakened to make an algorithm look successful. The bead-level support checker is the authority.
2. **Valid route / construction topology is a hard validity constraint.** The required wall shells and routes stay valid and complete: required closed construction stays closed, required shells are never left open, and no invalid printable topology is produced. Rule 1 never licenses breaking topology, and rule 2 never licenses breaking support. BOTH are mandatory; neither is a preference to be traded against the other. Where no construction satisfies both, that is a REPORTED FAILURE, never a silent choice.
3. **Within valid solutions, maximise cross-Z stacking / correspondence.** Lattice stacks as directly as possible on the physical construction below, and zero unnecessary lateral displacement is preferred. Movement is allowed only where architectural change requires it, and then within the overhang constraint. Existing construction is NOT moved, re-phased, redistributed, regenerated or replaced while leaving it in correspondence with the construction below is still geometrically valid. Coherent stacking of lattice on lattice is one of the fundamental purposes of the lattice.
4. **Architectural change is resolved WHERE it occurs.** A moving wall end, corner, junction, opening jamb, cut boundary or other architectural transition consumes or exposes construction progressively from that location. Construction elsewhere stays the continuation of the construction below until the moving transition physically reaches it. When both ends of a wall move inward, BOTH ends consume the existing pattern; the lattice never flows across the wall from one end toward the other. A moving junction moves THROUGH the existing pattern; the remaining pattern is never redistributed between the new endpoints. Cell-count changes happen this way: locally, at the transition. They never happen as global re-fits, and never only through explicit designer-placed events.
5. **Preserve ancestry and recognisable structural character.** Surviving construction is the descendant of the construction physically below it. A zigzag stays recognisably the same zigzag and a wave the same wave, except where a local architectural transition requires a local motif / change. Topology bookkeeping (segment ids, run ids, anchor names, domain ids) never destroys physical ancestry: a renamed, split or merged segment is the same physical construction.
6. **Nominal pattern parameters are subordinate preferences through Z.** Target Web Angle, target spacing / pitch, ideal wavelength / period, equal spacing, symmetry and similar parameters describe the PREFERRED construction. They never justify breaking rules 1–5, and existing repeating construction is never globally refit through Z to recover a target. For surviving construction, wavelength / frequency / phase do NOT vary independently over Z and are NOT interpolated or regenerated per layer. These parameters define initial / new construction; surviving construction persists from below.
7. **Prefer simple, flowing local geometry when rules 1–6 permit.** Among solutions equal under the higher rules, prefer fewer bends, reversals, jogs and short corrective segments. A modest local deviation from a target angle beats down → over → back-up geometry added only to recover the nominal angle. This rule is low priority: it never outranks support, topology, stacking or ancestry.

**Decisions on the open questions (user, 2026-10-18; part of §Z.0):**
- **Support + topology:** both stay simultaneous validity constraints. A defined local motif (e.g. parallel lanes) may be used only if it satisfies BOTH; it is not an exception and outranks neither. If no available construction satisfies both, report failure. Never silently exceed Maximum Overhang or leave required topology invalid.
- **Junction / lens zones:** rule 3 applies inside them. A junction may use a special local motif, but that motif must itself be supported by, and evolve coherently from, the construction below. An unrelated fresh junction / lens motif is never regenerated independently on each layer. Architectural change may alter construction locally; it never waives cross-Z support and stacking.
- **In-plane maximum unsupported distance** (`max_unsupported`, along the wall between consecutive landings) is distinct from vertical Maximum Overhang. For genuinely new single-layer construction it is a structural design constraint. For surviving construction, exceeding it never triggers global redistribution / re-spacing. If changing architecture makes existing construction structurally unacceptable, resolve it locally with supported construction where possible, otherwise report a finding / failure.
- **Deliberate Wall System parameter changes** (a design or setting changes pitch, pattern, angle …) establish a new TARGET construction, never an unsupported one-layer reset. The construction below stays the physical support condition, and the construction transitions toward the new target over subsequent layers within Maximum Overhang, topology and the rest of §Z.0. Surviving construction is not globally re-phased at the design boundary.

**Clarifications (user, 2026-10-18; binding):**
- **Yesterday's valid deposited construction is today's construction.** The construction actually deposited on layer N is the known physical starting state of layer N + 1: it IS the ancestry and the support condition. Carrying it forward is intended. What stays forbidden is GUESSING ancestry: generating construction independently on two layers and then matching it by nearest distance or other geometry (the reverted Pass B, 2026-10-16). Construction that is still valid stays; only what the architecture forces to change, changes, and only locally.
- **Locked surviving construction.** A surviving landing / repeat moves only as required by (a) the host's legitimate rigid motion and (b) perpendicular accommodation to the changed physical wall face / rail. It never slides longitudinally along the wall to improve pitch, phase, angle, symmetry, parity or endpoint fit. All movement stays within Maximum Overhang.
- **The rig vs changing territory.** A genuine RIGID motion of the host (the whole wall translates or rotates) carries its construction with it, still subject to Maximum Overhang. If the rigid architectural motion itself exceeds printable overhang, report failure; never leave the lattice behind in world coordinates to manufacture artificial support. After the rigid part, any NON-RIGID change of the available territory (lengthening, shortening, scaling, re-shaping) is never distributed through surviving construction. Ends, corners, junctions, jambs and cuts consume or expose it locally.
- **Single-layer generation is the authority only for NEW construction.** This covers the first layer that prints a wall, territory a transition exposes, and local transition motifs. Its preferences (even spacing, nominal pitch, one pitch per run, minimum in-plane unsupported distance, symmetric landings) never override rules 3–5 for construction that already exists below.
- **Two distinct "unsupported" quantities; never conflate them:** the vertical MAXIMUM OVERHANG (an upper bead's lateral offset from the construction below, rule 1) and the in-plane MAXIMUM UNSUPPORTED DISTANCE along a wall (a single-layer constraint for new construction). The second never justifies redistributing surviving construction.

**Implementation precedence (user, 2026-10-18): product invariants outrank mechanisms.** Every implementation concept is a PROVISIONAL MECHANISM: combs, frames, anchor selection, hysteresis, territories, windows / pieces, segment identities, inheritance, parity / lane machinery, scaffolds, tracking, phase references, slack cells, deposited-landing records. If a mechanism conflicts with a requirement, change or remove the mechanism; never reinterpret the requirement to preserve it.

**Golden / test rule (user, 2026-10-17):** preserving current golden geometry is NOT a goal in itself. Intentional geometry changes are acceptable when they come from replacing obsolete or over-complicated machinery with a cleaner implementation of these rules; unexplained regressions are not. Never modify production geometry merely to make an obsolete test pass. Classify every failing test / golden before touching it: A, a valid invariant violated (fix the code, never paper over it); B, an expected change from approved work (update or re-baseline after review); C, a test that encodes superseded behaviour (rewrite it to the product relation); D, an unrelated regression (fix it).

### Z.1 Durable product invariants (2026-10-17; read with §Z.0, which governs where they differ)

1. **One continuous evolving construction.** A printed wall is ONE physical structure through Z: never independently solved 2D layers, never independent source shapes patched together afterwards. Architecture (Base / Variants + Assembly transforms, openings, cuts, walls meeting or separating) defines the available TERRITORY per layer. Wall Systems decide how that territory is physically constructed.
2. **Physical support from below** is the primary cross-Z requirement (now §Z.0 rule 1).
3. *(SUPERSEDED 2026-10-18 by §Z.0. The earlier priority order "(1) support, (2) ancestry / continuity, (3) local character, (4) ideal parameters" omitted route topology as a co-equal validity constraint.)* What survives of it: a nominal 20° web may run at 17° for some layers if support needs it.
4. **Shrinking territory consumes construction LOCALLY at the moving boundary** (corner, junction, opening, wall end, cut face, other topology boundary). Four cells becoming three means the END cell is progressively consumed; the surviving cells never redistribute (N → N − 1 without redistribution).
5. **Expanding territory grows out of surviving, supported construction.** New construction branches from what exists below and never restarts at an unrelated phase. **Short-territory ancestry:** a short brace / pier left between openings is a descendant of the larger web below it: it descends from that pattern (no new phase; the last surviving brace keeps its diagonal), and the web grows back out of it above (approved rule, 2026-10-16).
6. **Junctions are LOCAL topology events.** Walls meeting, separating or changing relationship through Z changes only the construction near that relationship. Unaffected construction continues as the same evolving construction. A junction that ceases simply ends, and the walls continue above as separate constructions (§F1 stands). Nothing is regenerated globally because of a junction.
7. **Same-system full walls form continuous construction DOMAINS across source boundaries.** A circle joined to a rectangle, both in one Interleaved / Linked / Chained Loop / Skin + Web system, is one connected territory, not "circle pattern + rectangle pattern + patch".
8. **Construction ROLE survives joining (THIN excursion distinction).** A THIN Out-and-Back wall stays an excursion when it meets a full Wall System and is never absorbed into Skin + Web, Interleaved, etc. The M7 rule stands: the continuous host strand the branch intercepts diverts out, returns and continues as itself (§F3: the host is resolved geometrically per layer). THIN walls never join a full-wall domain.
9. **Different Wall Systems remain themselves and meet through LOCAL junction motifs / transitions.** The junction mediates locally, and neither system is regenerated because of the other. A junction may use its own simple local construction motif, and that motif follows §Z.0 (supported by, and evolving from, the motif below).
10. **Simple parallel-lane transitions are a valid canonical motif.** A patterned system may resolve into parallel lines approaching / through a junction or difficult corner; those lines wrap / connect cleanly, and the pattern resumes afterwards (Interleaved already does this at corners). This is one motif among possible ones. It is not a requirement that every junction use it, nor an attempt to blend two whole systems mathematically.
11. **Smooth curves remain curves.** Tight curvature alone is never a corner, and a circle has zero corners at any radius. Real corners may use local motifs. (Whether a sufficiently rounded architectural corner is best treated as a curve is an OPEN product rule.)
12. **Authoring alignment never defines physical connectivity.** Inside / Center / Outside are authoring references. A wall must be able to connect to EITHER physical face of another wall, whatever reference its host was drawn from; connectivity belongs to the physical / canonical wall. (Not satisfied by the baseline.)
13. **Physical bead / contact rules govern construction and routing** (§Z.2). Routing follows the declared construction (strand identity, join groups) and never infers topology from geometry afterwards.
14. **Existing interior construction does NOT refit** (user, 2026-10-17 evening; binding on every repeating Wall System). A repeating construction is never regenerated, redistributed, stretched, compressed or re-spaced because the available wall length changed through Z. Once it exists on a wall, its interior PHASE, WAVELENGTH / PITCH and GEOMETRY stay fixed relative to the continuing host architecture until a local architectural transition physically reaches it. Complete interior peaks / nodes / crossings keep their position and shape. WRONG: scaling the wavelength with the wall; recomputing how many repeats fit; redistributing N (or N − 1) repeats evenly; moving interior stations because the endpoints moved; an interior "slack cell" that stretches / compresses to absorb endpoint movement.
15. **Architectural transitions consume and expose construction LOCALLY.** The boundary that absorbs a length change is a LOCAL ARCHITECTURAL EVENT (wall end / end cap, real corner, junction transition zone, opening jamb, profile / cut boundary, another explicit local topology transition), never the ordinary longitudinal wall faces. A moving end / corner / junction moves THROUGH the existing construction: whatever portion of the pattern it reaches is truncated / resolved into that end cap / corner / junction treatment, and only the local transition between the moving boundary and the still-existing pattern is re-solved. Once a repeat is wholly consumed it is gone, and everything farther inside is unchanged. When both ends move, both consume locally. A moving junction reads NORMAL PATTERN → local transition → junction motif → local transition → NORMAL PATTERN, and only the transition moves. A topology change at one junction never reorganises construction elsewhere.
16. **Expansion is the inverse.** When a wall end moves outward, an opening closes or a junction exposes territory, existing construction, wavelength and phase stay unchanged. New repeats appear only in the newly available territory next to the moving boundary, growing from the existing pattern, and existing repeats are never spread apart.
17. **Assembly transforms: rigid motion moves construction with the wall; a changing distance between architectural events never scales the construction between them.** A translated wall's construction translates with it, and a rotated wall's rotates with it. A 100 in wall with a 20 in period shortened to 50 in about its centre keeps a ≈ 20 in period, with both ends consuming repeats. **Physical Wall Thickness does NOT scale with an Assembly plan-scale transform:** an 8 in wall stays 8 in on every layer unless Wall Thickness itself changes. (VIOLATED by the baseline: a section `scale_in` / assembly-wide scale is applied as an `InstanceTransform` of the FINISHED layer geometry, which scales the printed thickness.)
18. **Curves persist too; closed loops have ONE stable seam.** Through Z, existing construction on a curve is never regenerated to recover an ideal web angle. A circle changing radius keeps its pattern identity and phase. Its construction moves only as required to remain on the host; it never rotates / re-indexes because a different integer count now fits, and never redistributes evenly when one repeat must go. A repeat is born or dies on a closed loop LOCALLY at ONE stable construction seam (the loop's stable route origin / seam, carried by identity), and the other repeats keep their phase and order.

### Z.2 Physical wall width and material decisions (user, 2026-10-16; NOT implemented in the baseline)

- **Wall Thickness = the FINISHED DEPOSITED width**, outside bead edge to outside bead edge, for every construction, never centreline spacing. The architectural envelope is that width and stays authoritative; bead centrelines lie inside it. (Baseline: generated systems already keep centrelines ½ bead inside the faces; Hollow and Skin + Web print the envelope FACES as centrelines, so a "7 in" skin wall deposits 10 in.)
- **Bead Width** = one deposited pass. Toolpaths are bead CENTRELINES.
- **Contact Overlap (O)** = overlap where a bead intentionally LANDS against another (centrelines W − O apart). It explicitly includes web → skin landings and never applies to ordinary crossings.
- **Parallel Wall Overlap** = the user-facing name of the existing `return_overlap` (R; storage key unchanged; baseline UI label "Return-lane overlap"). It is the MAXIMUM overlap of neighbouring paths running alongside each other (THIN Out-and-Back lanes, Parallel Walls lanes): a maximum, not a target. O and R stay separate concepts.
- **THIN** = 2W − R (5.25 in at W 3, R 0.75): derived, never a free thickness.
- **UI decision:** the Wall Systems workspace gets a compact "Material · shared with Designer" section (Bead Width, Contact Overlap, Parallel Wall Overlap) that edits the SAME project material state as the Designer, never a copy.
- **Open product questions raised with this decision:** Parallel Walls default 3 lanes in a 7 in wall overlap 1.0 in > the 0.75 max (lane-count rule vs report); whether the envelope should equal the full build's material regions for skin walls (with insetting, a skin wall's print region is the inset band); whether a Route Origin stored on an aligned skin wall's authored reference (no longer a printed strand once skins are inset) is re-attached to the near printed skin or kept by the "never moved onto other geometry" rule; end caps / opening jambs still centre a bead on the cut / end line. A Skin + Web wall too thin for a cavity (e.g. 6 in at a 3 in bead) prints skins only: intended.
- **Earlier implementation (archived, reference only):** `931d4b8` (skins inset ½ bead in print builds only, envelope unchanged), `bfce435` (material section). Re-porting either is a future milestone of its own.

### Z.3 Skin + Web single-layer decisions (user, 2026-10-16; NOT implemented in the baseline)

- **Curves are not corners.** A continuous curve is never a corner merely because it is tight. A circle has zero corners at any radius, with no radius threshold, and one shared corner detector serves the lattice and the generated systems. Rounded architectural corners / junctions were not redesigned (§Z.1 11: open).
- **Target Web Angle** replaces fixed pitch for Skin + Web zigzag / wave webs. The angle is measured from the wall line across the CAVITY between the contact rails, so pitch follows angle + measured cavity: S = (skin-centreline separation − 2 (W − O)) · cot(angle). There is no arbitrary minimum thickness. The default is 20° (range 10–75°), and the floor is a pitch of max(1, W/2), reported as "angle limited". Contact Overlap landings are kept. Unowned legacy wall infill keeps Target Spacing, and the Adaptive Truss keeps its Brace Angle. Saved webs keep their stored `spacing` (ignored for Skin + Web) and a missing `web_angle` means 20°, with no migration.
- **Curved Skin + Web: the CONCAVE skin owns the angle.** Each brace leaves the inside contact rail at the angle to that rail's local tangent and runs straight to the convex rail; the convex landing and angle follow (no equal spacing on both skins). Straight walls, sharp corner motifs and junction-adjacent stations keep the straight-wall rule.
- **UI decision:** the Skin + Web web panel (Designer + Wall Systems) shows Target Web Angle (°) instead of Target Spacing, with the readout "Pitch: … in (Target Web Angle …° across a … in cavity)" and "angle limited" when the floor binds.
- **Scope:** these are SINGLE-LAYER rules for NEW construction (§Z.0 rule 6; "landings evenly spaced round a ring" is a single-layer preference). Through Z, surviving construction persists (§Z.1 14, 18). Short territories follow §Z.1 5.
- **Open:** the dense web at very thin cavities (floor W/2 or a Parallel-Wall-Overlap rule?); one derived pitch per region (median cavity) vs per run.
- **Earlier implementation (archived, reference only):** `e3e93dd` (Pass A).

### Z.4 Historical rules now SUPERSEDED (history, not direction)

These rules / mechanisms exist in the baseline's code or older memory text. They stay where they are so the code can be understood, but they are not direction: do not extend them, and replace them as milestones reach them.

| Superseded rule / mechanism (date) | Where it lives in the baseline | Governed now by |
|---|---|---|
| "A closed route outranks registration" (2026-10-06) — `scaffold_verdict` / planned-alone | `layer_design.py`; Designer memory, Z PHASE B | §Z.0 rules 1–2: route topology and support are BOTH validity constraints |
| Lattice stations / identity carried from the UNTRANSFORMED reference plan by geometric run matching (2026-10-06): `lattice_track`, `_track_map`, `_match_track`, `_tracked_counts`, partial tracking + parity repair | `layer_design.py`, `wall_lattice.plan(track=…)`, `model.py` | §Z.0 rules 3–5 (the deposited construction below is the ancestry; never matched geometrically) |
| One shared lineage SCAFFOLD per lattice family, planned with all openings removed, clipped to each member (2026-10-06): `scaffold`, `lattice_lineage`, `_station_reference` probes | `layer_design.py` | the idea "clip existing construction at the jamb" fits §Z.0 rule 4; the separate scaffold / verdict / planned-alone machinery is not direction |
| Generated Wall Systems keep phase at cuts through a PHASE REFERENCE of the uncut layer, and per-span period stretching `n = round(len / period)`, `P = len / n` (2026-10-08): `phase_ref`, `wall_system_reference`, `ws_frames`, `_wave_paths` | `wall_systems.py`, `layer_design.py` | §Z.0 rules 3–6, §Z.1 14–16: persistent period / phase, consumed locally at transitions |
| "The generator re-phases a ring on any envelope change" (Interleaved / Linked / Chained Loop) | `wall_systems._wave_paths` | §Z.1 14, 18 |
| Even REDISTRIBUTION of stations inside a segment when a count changes; "segment counts raised and stations redistributed until no gap exceeds the maximum unsupported distance" (pass 7) | `wall_lattice.plan` / `seg_options` | single-layer rule for NEW construction only (§Z.0, two unsupported quantities) |
| "One global pitch", "N = nearest integer to U / S, pitch = U / N exactly", "evenly distributed stations" | `infill.py`, `wall_lattice` | single-layer layout of new construction only |
| Keyframe "parametric interpolation: interpolate procedural parameters and regenerate" and "web parameters (amplitude, wavelength, phase) vary over Z", read as applying to lattice through Z | Designer memory, Core Design Decisions (FORM concept, not built) | §Z.0 rule 6: apply to FORM; surviving construction persists |
| "Lattice cell-count changes must be explicit designer-placed transition events" | Designer memory, Core Design Decisions | §Z.0 rule 4: consumed / exposed locally by moving transitions (designer events remain for deliberate design changes, which set a new target) |
| "There are no cross-Z hints in the Designer ↔ Assembly contract" (the Designer tracks against the untransformed design itself) | layer_assembly memory, contract | §Z.0: each layer starts from the construction deposited below; how the contract carries it is an open design question for a future milestone |
| "Transformed-junction lattice — next pass: carry reference lattice GEOMETRY wall-relatively / track against the previous layer's plan" (2026-10-06 proposal) | Designer memory | the diagnosis stands; the proposal is replaced by §Z.0 + clarifications |
| A section `scale_in` / assembly-wide scale applied as an `InstanceTransform` of the finished layer geometry | `layer_assembly/model.py` | §Z.1 17: Wall Thickness never scales with a plan scale |
| Hollow / Skin + Web envelope FACES printed as bead centrelines | `model.py` (print builds) | §Z.2: Wall Thickness = finished deposited width |
| Fixed Target Spacing (20 in) for Skin + Web webs | `wall_lattice`, Skin + Web web params | §Z.3: Target Web Angle |

Kept for now with no conflict: the opening cross-Z transition ramps (`cut_transitions`, `profiles._transition_layers`) as the one top-down input; the local cut join (`_clip_to_rings` / `_join_cut_ends`), which already realises "clip and resolve locally" for Assembly cuts.

### Z.5 Roadmap / UI decisions recorded with the specification (2026-10-17; NOT scheduled)

- Wall Systems Top View must not change zoom while scrubbing layers.
- A wall must be attachable to EITHER physical face of another wall, independent of authoring alignment (§Z.1 12); this needs a canonical derived wall topology / centreline (with the full-wall junction work).
- Header-needed analysis belongs AFTER the Wall System construction is known (Wall Systems or Print Simulation). The Assembly cannot know whether separate skins need separate headers.
- Avoid local zigzag transition jogs (down → sideways → back up) where one clean continuous diagonal makes the same connection (§Z.0 rule 7).
- End treatments persist through Z: an end cap never appears on one layer without a supporting predecessor.
- M8 (contact overlap at excursion landings) and M9 (junction-preserving transform resolution, §5) are orthogonal to §Z and stay in the plan.

### Z.6 The archived attempts: what was learnt (history; NOT architecture)

Kept so future milestones do not repeat them. Full records: `design-toolpath/WALL_ARCHITECTURE.md` §Z.2–Z.12 at commit `bfb0e93`.

- **Pass B (2026-10-16, reverted before any commit):** per-layer station carrying with nearest-distance matching. Identity cannot be recovered geometrically; it was slow and still did not stack.
- **Z0–Z2 (2026-10-17, committed `428dd72`–`8997650`, not in the baseline):** a classified green baseline; a cross-Z coherence / support HARNESS measuring the product requirement on deposited geometry (bead-level support per layer pair, construction correspondence to the layer below, topology events, NONLOCAL reorganisation away from moving boundaries); construction identity naming (junction records, corner ordinals, wall ends, jambs; domains and segments). Byte-identical geometry. The harness idea (measure the product, not a mechanism) is worth re-porting; whether the identity layer is needed is open.
- **Z3 / Z4 construction-state chain (2026-10-17, uncommitted, then archived):** (a) a station field pinned at an anchor with pitch scaled with the wall + a slack zone. This violated §Z.1 14 (it refitted). (b) A "persistent comb" (physical pitch + phase in the host wall's frame, boundary transition cells with hysteresis). It worked on continuous walls but failed catastrophically at every topology event while the state was keyed by SEGMENT identity, because temporary segment identity owned ancestry (§Z.0 rule 5). (c) The comb re-keyed by host wall, with windows / pieces / a lens rule. It still failed manual testing on simple geometry.
- **Deposited-landings Milestone 1 (2026-10-18, archived):** each layer's web landings as deposited were carried to the next layer (rigid host motion carried them; otherwise a landing kept its world position, projected perpendicular onto its face's rail; each segment end consumed / promoted landings independently). Scope was a straight wall and a plain rectangle only. Simple Skin + Web stacking improved substantially, but taller transforms and complex geometry still failed manual review; junctions, closed curves and generated systems were never addressed; the FAST run hung (layer_assembly cancelled after 4 h 16 min).
- **Lessons:** keep each milestone small and visually reviewable; measure deposited geometry, not a proxy; never let bookkeeping identity own ancestry; mechanisms piled on mechanisms (two competing frames, three parity fixes) signalled the wrong model.

### Z.7 How work proceeds from here

Small, manually reviewed milestones on the `8988108` baseline, each its own checkpoint, each judged against §Z.0 (and §F for junction ownership). The first milestone is NOT yet chosen. **Candidates (not decided), for the user to choose from:** re-porting physical wall width (§Z.2), the shared material controls, Skin + Web single-layer correction (§Z.3), a coherence / support harness (§Z.6), test cost tiers (`1610f95`), the behaviour-preserving cleanup (`a11fcc1`); a first narrowly scoped through-Z stacking case.

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
- **Now** (`wall_systems.splice_excursion` on the shared `_splice` core of `connect_branch`; `model._connect_skins`, per built layer):
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
- **Not done (later milestones):** contact overlap at the landings (M8); transform-junction resolution (M9); excursions for thick (non-thin) incoming walls (unchanged `connect_branch`).

### M7 correction pass after manual review (2026-10-16; manually reviewed and checkpointed (2026-10-16), with M6 + M7)

- **Observed in review:** a thin Out-and-Back running THROUGH a Chained Loop host showed bracket / staple geometry at the crossing; Base and Variant / later layers differed; Chained Loop excursions came and went with the pitch.
- **The bracket = the pass-10 crossing loop** (`wall_systems.extend_through`): lanes continued past the host's face and capped inside, crossing the host's paths. M7 used it as the fallback whenever the host search failed. Two failures fed it:
  1. **Sequential splicing.** The arms of one generated part were spliced one after another, each on the geometry the previous ones had cut. A through wall's two arms (the network splits the line at the host) meet the host at the same station from opposite sides. On a Chained Loop both often reach the SAME stretch: the second arm then found only the first arm's cut, failed, and got the crossing loop. Fallback loops were also appended to the geometry later arms searched.
  2. **The Parallel-kiss guard.** `_splice` refuses any host stretch holding a vertex the strand visits twice. A Chained Loop's necks are such vertices (in multi-ring / junction layouts, e.g. circle × rectangle — 51 in the synthetic case, 48 in the user's `test6`). When a neck fell between the lanes, every candidate was refused.
- **Why Base / layers / pitch differed:** the loop pattern re-fits to every envelope change: pitch, a Variant opening, an Assembly cut. That moved the necks relative to the branch and changed which stretch each arm reached. The architecture never changed; the old search's outcome did. Reproduced on a copy of `test6` + a vertical line (old code: Base excursion, Variant bracket at pitch 7; the ending branch also failed at pitch 6). The new code gives excursions on Base, the Variant and every Assembly layer.
- **Now (`wall_systems.splice_excursions`, `model._connect_skins`):**
  - All Out-and-Back arms of a part are resolved TOGETHER on the unspliced generated geometry (`_resolve_host`, the unchanged host search of `_splice`), so the result is order-independent.
  - The kiss guard is not applied to excursions. Cutting through a neck is valid: each piece stays one polyline, and the other pass keeps the vertex. `connect_branch` (thick incoming walls) keeps the guard.
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

### Before M5 — to decide

1. *(M5: no longer arises in any golden case — those nodes were the unresolved trims' overlap; still open for genuine band overlaps without trims.)* **Band-overlap contacts** (production_only above): with source-line junctions, bands that overlap without their source lines touching are NOT a junction. Should M5 treat them as plain material union (no junction, no corner treatment), or keep a junction there?
2. **Per-corner shapes:** §4 has one `shape` per junction; legacy overrides are per corner (no real project uses any). Is one shape per junction enough? M4 keeps the per-corner list.
3. **Record storage:** move the records into the lineage-owned documents (as §4) — confirm. *(M5 did not need it: trims resolve by the identity rule and record the ids; records stay project-level.)*
4. ~~Re-baseline full golden hashes~~ — DONE at the M0–M4 checkpoint after the user's review. Before it, every strict difference was a full hash only; the label-stripped `geom` hashes and `.mudjob` were equal. `GOLDEN_STRICT=1` now passes 23/23.
5. **Junctions that APPEAR only on transformed layers** stay unnamed (per-layer topology, §F1) — confirm that no record or treatment is wanted for them.


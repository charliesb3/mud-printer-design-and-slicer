"""
Material / Bead — the PHYSICAL deposit around a printable centerline.

Three distinct concepts:
  * Wall Thickness — architectural thickness of a wall assembly (design).
  * Centerline     — the vector path the nozzle follows (toolpath).
  * Bead Width     — physical width of ONE deposited mud pass (material).

The bead footprint of a centerline is the centerline swept by a disk of
the bead width (Minkowski sum): a strip of half-width w / 2 with ROUND
ends and round joins — a straight open line is a capsule. The footprints
of all printable centerlines together are the deposited material
(overlapping beads simply union).

First pass (2026-10-05): Bead Width is VISUALISATION ONLY. It is carried
with the layer (an input the geometry generators can read later) but does
not change walls, infill, lattice, routing, trims, openings or junctions.

Future rules (NOT implemented): bead height, minimum bend radius,
congestion (too many beads meeting).

Second pass (2026-10-06) — PHYSICAL RULES (`physical`; the app turns them
on, the bare engine default stays the zero-width legacy for the generic
toolpath prototype):
  * CONTACT OVERLAP (O): how deeply a generated bead overlaps a printed bead
    it is meant to LAND AGAINST. Centreline separation at a side contact =
    W − O (O = 0: tangent; O = W: centrelines coincide). First user: the
    wall lattice — interior stitch landings stop W − O off the face
    centreline. It does NOT apply to CROSSINGS (centrelines meant to cross).
  * RETURN-LANE OVERLAP (R): overlap between two adjacent passes where the
    second pass replaces what would have been a retrace. Centreline
    separation = W − R (≥ RETURN_MIN: R = W would be an exact retrace).
    First user: a single-bead open wall becomes a TWO-PASS wall (passes
    W − R apart, semicircular turnaround), physical width ≈ 2W − R.
  * EXACT RETRACE IS FORBIDDEN and every connected printable component must
    close (start = end, zero travel inside it) — the generators make the
    geometry closable; the router never retraces and REPORTS any component
    it cannot close instead of hiding it.
The two overlaps share the formula W − overlap but are separate physical
operations (machine testing may give them very different values): keep
them separate everywhere.
"""
from __future__ import annotations
from dataclasses import dataclass

DEFAULT_BEAD_WIDTH = 3.0      # in
BEAD_WIDTH_STEP = 0.25        # in (UI increment)
DEFAULT_CONTACT_OVERLAP = 0.75   # in (provisional: 25 % of the default bead)
DEFAULT_RETURN_OVERLAP = 0.75    # in (provisional: 25 % of the default bead)
RETURN_MIN = BEAD_WIDTH_STEP     # smallest return-lane centreline separation


@dataclass
class MaterialSpec:
    bead_width: float = DEFAULT_BEAD_WIDTH
    contact_overlap: float = DEFAULT_CONTACT_OVERLAP
    return_overlap: float = DEFAULT_RETURN_OVERLAP
    physical: bool = False        # physical rules on (the app sends True)

    def __post_init__(self):
        self.bead_width = max(BEAD_WIDTH_STEP, float(self.bead_width))
        self.contact_overlap = min(max(0.0, float(self.contact_overlap)), self.bead_width)
        self.return_overlap = min(max(0.0, float(self.return_overlap)),
                                  self.bead_width - RETURN_MIN)

    def contact_separation(self) -> float:
        """Centreline distance of an intentional side CONTACT: W − O."""
        return self.bead_width - self.contact_overlap

    def return_separation(self) -> float:
        """Centreline distance between a pass and its RETURN LANE: W − R."""
        return max(RETURN_MIN, self.bead_width - self.return_overlap)

    def two_pass_width(self) -> float:
        """Physical width of an out-and-back (two-pass) wall: 2W − R."""
        return self.bead_width + self.return_separation()

    def to_dict(self) -> dict:
        return {'bead_width': self.bead_width, 'contact_overlap': self.contact_overlap,
                'return_overlap': self.return_overlap, 'physical': self.physical}

    @staticmethod
    def from_dict(d) -> 'MaterialSpec':
        d = d or {}
        num = lambda k, dflt: float(d.get(k, dflt) if d.get(k) is not None else dflt)
        return MaterialSpec(bead_width=num('bead_width', DEFAULT_BEAD_WIDTH) or DEFAULT_BEAD_WIDTH,
                            contact_overlap=num('contact_overlap', DEFAULT_CONTACT_OVERLAP),
                            return_overlap=num('return_overlap', DEFAULT_RETURN_OVERLAP),
                            physical=bool(d.get('physical', False)))


def footprint_distance(p, pts, closed) -> float:
    """Distance from p to a centerline (polyline)."""
    import network as N
    return N.dist_to_polyline(p, pts, closed)


def in_footprint(p, pts, closed, width) -> bool:
    """Is p inside the bead footprint of this centerline? (round caps and
    joins: every point within width / 2 of the centerline)."""
    return footprint_distance(p, pts, closed) <= width / 2.0 + 1e-9

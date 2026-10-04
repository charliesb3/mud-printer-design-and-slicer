"""
Five test geometries for the toolpath prototype.

All coordinates are in abstract units.  For context, imagine 1 unit ≈ 1 inch.
Each geometry represents a horizontal cross-section (top-down view) of a
print layer.

A – single continuous wall          (1 closed loop,  0 odd nodes, circuit)
B – two independent walls           (2 closed loops, 0 odd nodes per comp,
                                     1 travel move between components)
C – outer + inner wall, unconnected (2 closed loops, same as B structurally)
D – wall perimeter + internal web   (1 connected graph, 2 odd nodes, path —
                                     the key architectural case)
E – awkward geometry: open paths    (mixed components with multiple odd nodes,
    + disconnected elements          requires augmentation + travel moves)
"""
from geometry import Vec2, Strand, Layer, Role


def case_a() -> Layer:
    """Single rectangular closed loop — trivially Eulerian circuit."""
    pts = [
        Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50),
    ]
    strand = Strand('wall', Role.OUTER, pts, closed=True)
    return Layer(z_height=0.0, strands=[strand],
                 label='A – Single closed wall')


def case_b() -> Layer:
    """Two separate closed rectangles — Eulerian within each, 1 travel between."""
    s1 = Strand('wall_1', Role.OUTER,
                [Vec2(0, 0), Vec2(40, 0), Vec2(40, 50), Vec2(0, 50)],
                closed=True)
    s2 = Strand('wall_2', Role.OUTER,
                [Vec2(60, 0), Vec2(100, 0), Vec2(100, 50), Vec2(60, 50)],
                closed=True)
    return Layer(z_height=0.0, strands=[s1, s2],
                 label='B – Two independent walls')


def case_c() -> Layer:
    """
    Outer + inner wall with NO lattice connection.
    Structurally identical to B — two Eulerian components, 1 travel.
    """
    outer = Strand('outer', Role.OUTER,
                   [Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)],
                   closed=True)
    inner = Strand('inner', Role.INNER,
                   [Vec2(15, 10), Vec2(85, 10), Vec2(85, 40), Vec2(15, 40)],
                   closed=True)
    return Layer(z_height=0.0, strands=[outer, inner],
                 label='C – Outer + inner wall, no lattice')


def case_d() -> Layer:
    """
    Wall perimeter + internal zigzag web — the core architectural case.

    Cross-section layout (top-down view, Y axis = wall thickness):

        (0,10)──(25,10)──(50,10)──(75,10)──(100,10)
          |        ╲        ╱╲        ╱╲        |
          |         ╲      ╱  ╲      ╱  ╲       |
          |          ╲    ╱    ╲    ╱    ╲      |
        (0,0)──(25,0)──(50,0)──(75,0)──(100,0)

    The perimeter (outer closed strand) + the web (open strand) share six
    junction nodes.  Degree analysis gives exactly 2 odd-degree nodes —
    (25,10) and (75,0) — so a single Eulerian path traverses everything
    without any travel moves or retracing.
    """
    # Perimeter: explicit interior junction points where web connects
    perimeter_pts = [
        Vec2(0, 0), Vec2(25, 0), Vec2(50, 0), Vec2(75, 0), Vec2(100, 0),
        Vec2(100, 10),
        Vec2(75, 10), Vec2(50, 10), Vec2(25, 10), Vec2(0, 10),
    ]
    perimeter = Strand('perimeter', Role.OUTER, perimeter_pts, closed=True)

    # Web: zigzag between top and bottom faces.
    # Start node (25,10) and end node (75,0) will be the two odd-degree nodes.
    web_pts = [
        Vec2(25, 10), Vec2(25, 0),
        Vec2(50, 10), Vec2(50, 0),
        Vec2(75, 10), Vec2(75, 0),
    ]
    web = Strand('web', Role.LATTICE, web_pts, closed=False)

    return Layer(z_height=0.0, strands=[perimeter, web],
                 label='D – Wall perimeter + internal web')


def case_e() -> Layer:
    """
    Awkward geometry: three disconnected components with varying properties.

    Component 1: a closed square (Eulerian circuit, 0 odd nodes)
    Component 2: an open zigzag path (Eulerian path, 2 odd nodes at endpoints)
    Component 3: a T-intersection (3 odd-degree nodes → needs augmentation)

    Requires 2 inter-component travel moves and within-component augmentation
    for component 3.
    """
    # Component 1: closed square
    sq = Strand('square', Role.OUTER,
                [Vec2(0, 0), Vec2(30, 0), Vec2(30, 30), Vec2(0, 30)],
                closed=True)

    # Component 2: open zigzag
    zz = Strand('zigzag', Role.FREE, [
        Vec2(45, 0), Vec2(55, 20), Vec2(65, 0), Vec2(75, 20), Vec2(85, 0),
    ], closed=False)

    # Component 3: T-intersection — center node has degree 3 (odd)
    # Three arms meeting at (15, 60)
    arm1 = Strand('arm1', Role.FREE,
                  [Vec2(0, 60), Vec2(15, 60)], closed=False)
    arm2 = Strand('arm2', Role.FREE,
                  [Vec2(15, 60), Vec2(30, 60)], closed=False)
    arm3 = Strand('arm3', Role.FREE,
                  [Vec2(15, 60), Vec2(15, 80)], closed=False)

    return Layer(z_height=0.0,
                 strands=[sq, zz, arm1, arm2, arm3],
                 label='E – Awkward geometry (augmentation required)')


ALL_CASES: dict[str, Layer] = {
    'A': case_a(),
    'B': case_b(),
    'C': case_c(),
    'D': case_d(),
    'E': case_e(),
}


def get_case(case_id: str) -> Layer:
    return ALL_CASES[case_id.upper()]

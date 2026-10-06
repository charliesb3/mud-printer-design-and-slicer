// Physical-bead diagnostic fixtures for MANUAL inspection in the Design
// Canvas (same geometry as tests/physical_fixtures.py, used by
// tests/test_physical.py). Paste into the browser console on
// http://127.0.0.1:5051, then e.g.:
//   physicalFixture('wave_ring')        1. wave landing on a 10 in wall (closed ring)
//   physicalFixture('zigzag_ring')      2. zigzag landing
//   physicalFixture('x_walls')          3. true X crossing (4 arms, infill)
//   physicalFixture('thin_x')           3b. two single-line walls crossing
//   physicalFixture('dead_end_line')    4. one single-line wall: two passes, U-turns
//   physicalFixture('lone_wall')        6. lone wall with infill: second (return) wave
//   physicalFixture('star')             7. six-arm network with infill
//   physicalFixture('islands')          8. ring + a separate island line
//   physicalFixture('branch')           rect wall + dead-end branch (out-and-back)
//   physicalFixture('rect_branch')      REGRESSION: single-bead rect + open single-bead
//                                       branch + an opening (all one closed route)
//   physicalFixture('rect_branch_closed') the same without the opening
// Then vary Contact overlap / Return-lane overlap in Material / Bead (5.
// several Return-lane values on 'dead_end_line'). Turns Beads on.
function physicalFixture(name) {
  clearAll();
  const add = (type, fields, label, extra = {}) => {
    const p = { id: newId(), type, label, closed: !['LinePath', 'QuadBezierPath'].includes(type),
                role: 'free', visible: true, ...fields, ...extra };
    _computePrimitivePoints(p);
    layer.source_paths.push(p);
    return p;
  };
  const wall = t => ({ wall: { thickness: t, align: 'auto', print_reference: false } });
  const line = (a, b, label, extra) => add('LinePath', { start: a, end: b }, label, extra);
  const infill = (p, pattern) => { selectedId = p.id; addInfill(); layer.infills[layer.infills.length - 1].pattern = pattern; };
  const arms = (n, len, rot) => {
    const ps = [];
    for (let k = 0; k < n; k++) {
      const a = rot + 2 * Math.PI * k / n;
      ps.push(line([200, 200], [200 + len * Math.cos(a), 200 + len * Math.sin(a)], `Arm ${k + 1}`));
    }
    layer.network_walls.push({ id: newId(), path_id: ps[0].id, thickness: 10, align: 'auto' });
    infill(ps[0], 'zigzag');
  };
  switch (name) {
    case 'wave_ring': case 'zigzag_ring': {
      const R = add('RectanglePath', { x: 100, y: 140, w: 200, h: 120, rotation: 0 }, 'Rect 1', wall(10));
      infill(R, name === 'wave_ring' ? 'wave' : 'zigzag'); break;
    }
    case 'lone_wall': infill(line([40, 200], [320, 200], 'Wall', wall(10)), 'zigzag'); break;
    case 'dead_end_line': line([100, 200], [300, 200], 'Line 1'); break;
    case 'thin_x': line([100, 200], [300, 200], 'Line A'); line([200, 100], [200, 300], 'Line B'); break;
    case 'x_walls': arms(4, 110, 0); break;
    case 'star': arms(6, 120, 0.2); break;
    case 'islands': {
      const R = add('RectanglePath', { x: 100, y: 140, w: 200, h: 120, rotation: 0 }, 'Rect 1', wall(10));
      infill(R, 'zigzag'); line([20, 380], [120, 380], 'Island'); break;
    }
    case 'branch': {
      const R = add('RectanglePath', { x: 100, y: 140, w: 200, h: 120, rotation: 0 }, 'Rect 1', wall(10));
      const B = line([200, 260], [200, 340], 'Branch');
      layer.network_walls.push({ id: newId(), path_id: B.id, thickness: 10, align: 'auto' });
      infill(R, 'zigzag'); break;
    }
    case 'rect_branch': case 'rect_branch_closed': {
      const R = add('RectanglePath', { x: 100, y: 140, w: 200, h: 120, rotation: 0 }, 'Rect 1');
      line([300, 200], [380, 200], 'Line 1');
      if (name === 'rect_branch') layer.openings.push({ id: newId(), source_path_id: R.id, center_s: 100, width: 30,
                                                       end_treatment: 'inherit', z_min: null, z_max: null, label: '' });
      break;
    }
    default: throw new Error('unknown fixture ' + name);
  }
  selectedId = null;
  updatePathList(); updatePropPanel(); updateInfillList();
  toggleBeads(true);
  scheduleRefresh();
  return layer.source_paths.map(p => p.label);
}

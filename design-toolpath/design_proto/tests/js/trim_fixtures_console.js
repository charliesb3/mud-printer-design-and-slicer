// Trim diagnostic fixtures for MANUAL inspection in the Design Canvas.
// The same geometry as tests/trim_fixtures.py (used by tests/test_trim.py).
//
// Use: open http://127.0.0.1:5051, open the browser console, paste this whole
// file, then run one of:
//   trimFixture('circle_rect')        Rect 1 + Circle 1 centred on its right edge
//   trimFixture('circle_rect', 10)    the same with 10 in walls (Wall Thickness)
//   trimFixture('circle_rect_off')    circle moved/grown: contacts off the rect corners
//   trimFixture('strip_circle')       circle crossing a thin strip: 4 contacts
//   trimFixture('line_rect')          open line through a rectangle: 3 line sections
//   trimFixture('curve_line')         quadratic curve crossed twice by a line
//   trimFixture('bars')               two bars + connector (trim its middle → disconnected)
//   trimFixture('infill', 10)         10 in walls + wall infill on the circle
// Each call replaces the current design (Clear All — undoable) and selects Trim.
function trimFixture(name, wall = 0) {
  clearAll();
  const add = (type, fields, label) => {
    const p = { id: newId(), type, label, closed: !['LinePath', 'QuadBezierPath'].includes(type),
                role: 'free', visible: true, ...fields };
    _computePrimitivePoints(p);
    if (wall) p.wall = { thickness: wall, align: 'auto', print_reference: false };
    layer.source_paths.push(p);
    return p;
  };
  const rect = (x, y, w, h, label = 'Rect 1') => add('RectanglePath', { x, y, w, h, rotation: 0 }, label);
  const circle = (cx, cy, radius, label = 'Circle 1') => add('CirclePath', { cx, cy, radius }, label);
  const line = (a, b, label) => add('LinePath', { start: a, end: b }, label);
  switch (name) {
    case 'circle_rect': rect(100, 100, 240, 160); circle(340, 180, 80); break;
    case 'circle_rect_off': rect(100, 100, 240, 160); circle(340, 150, 95); break;
    case 'strip_circle': rect(40, 160, 320, 40, 'Strip'); circle(200, 180, 60); break;
    case 'line_rect': rect(100, 100, 200, 120); line([40, 160], [360, 160], 'Line 1'); break;
    case 'curve_line':
      add('QuadBezierPath', { start: [60, 100], end: [340, 100], control: [200, 320] }, 'Curve 1');
      line([40, 160], [360, 160], 'Line 1'); break;
    case 'bars':
      line([100, 100], [300, 100], 'Line A'); line([100, 200], [300, 200], 'Line B');
      line([200, 60], [200, 240], 'Connector'); break;
    case 'infill': {
      rect(100, 100, 240, 160); const c = circle(340, 180, 80);
      selectedId = c.id; addInfill(); break;
    }
    default: throw new Error('unknown fixture ' + name);
  }
  selectedId = null;
  updatePathList(); updatePropPanel(); updateInfillList();
  scheduleRefresh();
  setTool('trim');
  return layer.source_paths.map(p => p.label);
}

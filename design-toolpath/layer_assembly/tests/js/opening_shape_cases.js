// Runs static/opening_shape.js on the cases given as JSON on stdin and prints
// the results as JSON (tests/test_opening_shape_parity.py compares them with
// layer_assembly/opening_shape.py).
const path = require('path');
require(path.join(__dirname, '..', '..', '..', 'design_proto', 'static', 'opening_shape.js'));
const O = globalThis.OpeningShape;
let buf = '';
process.stdin.on('data', d => { buf += d; });
process.stdin.on('end', () => {
  const out = JSON.parse(buf).map(([s, edited, lh, h, bead]) => {
    const [q, info] = O.constrain(s, edited, lh, h, bead);
    const ev = O.evaluate(q, lh, h, bead);
    const ws = ev.ok ? O.layerWidths(q.z_bottom, q.width, ev.d, ev.m, ev.U, q.upper, q.lower, lh, O.wmin(bead, h)) : [];
    return { q, info, ev, ws, lim: O.limits(s, lh, h, bead) };
  });
  process.stdout.write(JSON.stringify(out));
});

// NUMERIC FIELDS in a REAL browser (headless Chrome over the DevTools
// protocol) against the real app: repeated clicks at ONE screen position and
// press-and-hold step exactly like the field's own step / min, the − / +
// buttons never move when the value (and the panel around it) changes, and
// focus stays on the field. Run via tests/test_numeric_fields.py
// (argv: Chrome binary, app URL, scratch dir).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const [CHROME, URL, TMP] = process.argv.slice(2);
const ud = fs.mkdtempSync(path.join(TMP, 'chrome-'));
const port = 9400 + Math.floor(Math.random() * 400);
const ch = spawn(CHROME, ['--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`, `--user-data-dir=${ud}`,
                          '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws, failed = false;
const check = (c, m) => { if (!c) { failed = true; throw new Error('FAIL: ' + m); } console.log('ok  ' + m); };
try {
  let tabs;
  for (let i = 0; i < 100 && !tabs; i++) {
    try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(100); }
  }
  ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(r => { ws.onopen = r; });
  let id = 0; const pend = {};
  ws.onmessage = m => { const d = JSON.parse(m.data); if (pend[d.id]) { pend[d.id](d); delete pend[d.id]; } };
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error('page: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 600));
    return r.result.result.value;
  };
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.enable');
  await send('Page.navigate', { url: URL });
  for (let i = 0; i < 100 && !(await ev(`typeof window.NumericFields === 'object' && typeof asm === 'object'`).catch(() => false)); i++) await sleep(100);

  // the centre of a field's − / + button (sel: a CSS selector of the input, or a JS expression giving it)
  const btn = (expr, dir) => ev(`(() => { const i = ${expr}; const b = i.parentElement.querySelector('.num-${dir > 0 ? 'up' : 'down'}');
    const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height }; })()`);
  const mouse = (type, p) => send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 });
  const click = async p => { await mouse('mousePressed', p); await mouse('mouseReleased', p); };
  const val = expr => ev(`+(${expr}).value`);

  // ---- every number field is the same fixed control ----
  const all = await ev(`(() => { const f = [...document.querySelectorAll('input[type=number].prop-input')];
    return { n: f.length, wrapped: f.every(i => i.parentElement.classList.contains('num-field')),
             spin: f.every(i => i.parentElement.querySelectorAll('.num-step').length === 2) }; })()`);
  check(all.n >= 13 && all.wrapped && all.spin, `every number field (${all.n}) has the − / + control`);

  // ---- Designer: Bead width (step 0.25, min 0.25) ----
  await ev(`(() => { addPrimitive('RectanglePath'); const R = layer.source_paths[0]; _wsSetType(_wsNew([R.id]), 'hollow');
    historyCheckpoint(); scheduleRefresh(); document.querySelectorAll('details').forEach(d => d.open = true); return 1; })()`);
  await sleep(300);
  await ev(`document.getElementById('bead-width').scrollIntoView({ block: 'center' }), 1`);
  await sleep(100);
  const BW = `document.getElementById('bead-width')`;
  const p0 = await btn(BW, 1);
  check(p0.w >= 18 && p0.h >= 20, `large step targets (${p0.w}×${p0.h} px)`);
  const bw0 = await val(BW);
  for (let k = 0; k < 3; k++) { await click(p0); await sleep(60); }
  check(Math.abs(await val(BW) - (bw0 + 0.75)) < 1e-9, 'Designer Bead width: three clicks at one spot = three steps of 0.25');
  const p1 = await btn(BW, 1);
  check(p1.x === p0.x && p1.y === p0.y, 'the + button did not move');
  const dn = await btn(BW, -1);
  for (let k = 0; k < 30; k++) { await click(dn); await sleep(15); }
  check(await val(BW) === 0.25, 'stepping down stops at the field minimum (0.25)');

  // ---- Assembly: rebuilt panels ----
  await ev(`setWorkspace('assembly'), 1`);
  for (let i = 0; i < 100 && !(await ev(`!!asm.resolved && !asm.resolving`)); i++) await sleep(100);
  const H0 = `document.querySelector('#asm-sections .asm-section .num-field input')`;
  await ev(`(${H0}).scrollIntoView({ block: 'center' }), 1`);
  await sleep(100);
  const hp = await btn(H0, 1), h0 = await val(H0);
  const pos = [];
  for (let k = 0; k < 4; k++) {
    await click(hp); await sleep(120);
    pos.push(await btn(H0, 1));
  }
  check(await val(H0) === h0 + 4 && await ev(`asm.state.sections[0].height`) === h0 + 4,
        'section Height (panel rebuilt after every click): four clicks = four steps');
  check(pos.every(q => Math.abs(q.x - hp.x) < 0.5 && Math.abs(q.y - hp.y) < 0.5), 'its + button stayed under the pointer');
  check(await ev(`document.activeElement === ${H0}`), 'focus is on the (rebuilt) field');
  // press-and-hold across rebuilds
  const before = await val(H0);
  await mouse('mousePressed', hp);
  await sleep(1300);
  await mouse('mouseReleased', hp);
  const held = await val(H0);
  await sleep(400);
  check(held - before >= 6 && held - before <= 20, `press-and-hold repeats (${held - before} steps in 1.3 s)`);
  check(await val(H0) === held && !(await ev(`NumericFields.state().hold`)), 'release stops it');
  // Section transform: the summary line appearing must not push the fields down
  await ev(`asmToggleTransform(0), 1`);
  await sleep(100);
  const SX = `[...document.querySelectorAll('#asm-sections .asm-section')][0].querySelectorAll('.asm-tf input[type=number]')[0]`;
  await ev(`(${SX}).scrollIntoView({ block: 'center' }), 1`);
  await sleep(100);
  const sp = await btn(SX, 1), sx0 = await val(SX);
  await click(sp); await sleep(150);
  const sp2 = await btn(SX, 1);
  await click(sp2); await sleep(150);
  const step = await ev(`ASM_STEP`);
  check(Math.abs(await val(SX) - (sx0 + 2 * step)) < 1e-9 && Math.abs(sp2.y - sp.y) < 0.5,
        'Shift X: the transform summary appears, the field stays put — two clicks = two steps');
  // the Layer height (step 0.05): stepping snaps to the step grid like the native spinner
  const LH = `document.getElementById('asm-layer-height')`;
  await ev(`(${LH}).scrollIntoView({ block: 'center' }), 1`);
  await sleep(100);
  await ev(`(${LH}).value = '1.52', 1`);
  await click(await btn(LH, 1)); await sleep(150);
  check(Math.abs(await val(LH) - 1.55) < 1e-9, 'Layer height 1.52 + one step → 1.55 (step grid, as native)');
  // a much longer / shorter value never moves the field or its buttons
  const lp0 = await btn(LH, 1), lm0 = await btn(LH, -1);
  await ev(`(() => { const i = ${LH}; i.value = '12345.25'; i.dispatchEvent(new Event('input')); return 1; })()`);
  const lp1 = await btn(LH, 1), lm1 = await btn(LH, -1);
  await ev(`(() => { const i = ${LH}; i.value = '1.55'; i.dispatchEvent(new Event('input')); return 1; })()`);
  check(lp0.x === lp1.x && lp0.y === lp1.y && lm0.x === lm1.x, 'the field never moves or resizes with its value');
  // all fields: one width per kind, independent of the text beside them
  const widths = await ev(`[...new Set([...document.querySelectorAll('#ws-assembly .num-field')].filter(f => f.offsetParent).map(f => f.getBoundingClientRect().width))]`);
  check(widths.every(w => w >= 66 && w <= 88), `field widths (${widths.join(', ')} px)`);

  // ---- Simulation: print speed (step 0.1) while panels refresh ----
  await ev(`setWorkspace('simulation'), 1`);
  for (let i = 0; i < 200 && !(await ev(`!!sim.data && !sim.loading`)); i++) await sleep(100);
  const PS = `document.getElementById('sim-print-speed')`;
  await ev(`document.getElementById('sim-speed-unit').value = 'in/s'; simSetSpeedUnit('in/s'); 1`);
  const ps0 = await val(PS), pp = await btn(PS, 1);
  await click(pp); await sleep(200); await click(pp); await sleep(200);
  check(Math.abs(await val(PS) - (ps0 + 0.2)) < 1e-6 && Math.abs(await ev(`asm.state.print_speeds.print`) - (ps0 + 0.2)) < 1e-6,
        'Simulation print speed: two clicks = two steps, applied (re-timed)');
  console.log('BROWSER NUMERIC FIELDS PASSED');
} catch (e) {
  console.error(e.stack || String(e));
  failed = true;
} finally {
  try { ws && ws.close(); } catch {}
  ch.kill();
  await sleep(300);
  try { fs.rmSync(ud, { recursive: true, force: true }); } catch {}
  process.exit(failed ? 1 : 0);
}

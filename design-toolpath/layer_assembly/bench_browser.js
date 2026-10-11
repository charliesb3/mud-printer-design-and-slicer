// BROWSER benchmark of the Assembly / Print Simulation workspaces (a
// development tool, not a test): the REAL page in headless Chrome, driven over
// the DevTools protocol (Node ≥ 22: built-in WebSocket), with the
// mixed-feature building of bench_mixed.py opened as a project file.
//
//   design_proto/.venv/bin/python -m layer_assembly.bench_mixed --project /tmp/bench.json
//   (a server: cd design_proto && .venv/bin/python -c "import app; app.app.run(port=5077, threaded=True)")
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
//       --remote-debugging-port=9333 --user-data-dir=/tmp/bench-chrome about:blank &
//   node layer_assembly/bench_browser.js http://127.0.0.1:5077/ /tmp/bench.json 9333
//
// Per step: `sync_ms` — the edit call's synchronous part (what blocks the
// page: state change, live preview, panels); `exact_ms` — until the exact
// resolve is shown (server + variant fetch + render); `server` stage
// timings (window.ASM_TIMING); redraw cost of the Assembly view; Simulation
// start-up (plan + ingest) and workspace switches. Since the DEFERRED PRINT
// PATHS (2026-10-11) Assembly edits resolve ENVELOPES (`envelope_s` = the
// server's envelope stage) and the Simulation plans only on "Generate Print
// Paths" (print preparation: every full variant + routing + bead-level
// support); an Assembly edit is also timed WHILE a preparation runs.
// (Simulation playback
// frames are not drawn by headless Chrome here — measured separately, see
// the memory's Print Simulation entry.)
const [, , URL_, PROJECT, PORT = '9333'] = process.argv;
const fs = require('fs');

async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(ok => ws.addEventListener('open', ok));
  let seq = 0;
  const pending = {};
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending[m.id]) { pending[m.id](m); delete pending[m.id]; }
  });
  const send = (method, params = {}) => new Promise(ok => { const id = ++seq; pending[id] = ok; ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, timeout: 900000 });
    if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 800));
    return r.result.result.value;
  };
  await send('Page.enable');
  await send('Page.navigate', { url: URL_ });
  await new Promise(ok => setTimeout(ok, 3000));
  await evaluate(`new Promise(ok => { const w = () => (typeof openProjectText === 'function' && window.AssemblyProject) ? ok() : setTimeout(w, 100); w(); })`);
  const text = fs.readFileSync(PROJECT, 'utf8');
  const rows = [];
  const row = r => { rows.push(r); console.log(JSON.stringify(r)); };

  // helpers in the page
  await evaluate(`
    window.ASM_TIMING = 1;
    window.__idle = () => new Promise(ok => { const w = () => (!asm.resolving && !asm._resolveLoop) ? ok() : setTimeout(w, 20); w(); });
    window.__step = async (fn) => {
      const t0 = performance.now();
      const p = fn();
      const sync_ms = performance.now() - t0;
      await p; await __idle();
      const exact_ms = performance.now() - t0;
      return { sync_ms, exact_ms, timing: asm.timing, error: asm.error, layers: asm.resolved && asm.resolved.total_layers,
               variants: Object.keys(asm.variants || {}).length };
    };
    window.__redraw = (fn, n = 5) => { const ts = []; for (let i = 0; i < n; i++) { const t = performance.now(); fn(); ts.push(performance.now() - t); }
                                       ts.sort((a, b) => a - b); return ts[Math.floor(n / 2)]; };
    true`);
  await evaluate(`openProjectText(${JSON.stringify(text)}, 'bench.json')`);
  await new Promise(ok => setTimeout(ok, 1000));

  const pick = r => ({ sync_ms: +r.sync_ms.toFixed(1), exact_ms: +r.exact_ms.toFixed(0),
                       server_total_s: r.timing && r.timing.server && r.timing.server.total,
                       variant_geometry_s: r.timing && r.timing.server && r.timing.server.variant_geometry,
                       envelope_s: r.timing && r.timing.server && r.timing.server.envelope_geometry,
                       support_s: r.timing && r.timing.server && r.timing.server.support_analysis,
                       variant_fetch_ms: r.timing && Math.round(r.timing.variant_fetch_ms),
                       render_ms: r.timing && +r.timing.render_ms.toFixed(1), error: r.error, variants: r.variants });
  // DESIGNER (2026-10-11: architecture only — the Wall Groups' envelopes): a
  // Wall Group thickness edit until its envelope is shown, vs the previous
  // Designer (full preview: Wall Systems + lattice + route)
  const designerEdit = mode => evaluate(`(async () => {
    window.DESIGNER_PRINT_PREVIEW = ${mode === 'preview'};
    showToolpath = ${mode === 'preview'};
    setWorkspace('designer');
    const g = layer.wall_groups[0];
    const once = async (dt) => {
      const before = envelopeLines, rb = routeResult;
      const t0 = performance.now();
      g.thickness = g.thickness + dt; routeResult = null; scheduleRefresh({ supersede: true });
      await new Promise(ok => { const w = () => ((${mode === 'preview'} ? (routeResult && routeResult !== rb) : envelopeLines !== before) && !_reqBusy)
        ? ok() : setTimeout(w, 10); w(); });
      return performance.now() - t0;
    };
    const ms = [await once(0.5), await once(-0.5), await once(0.5), await once(-0.5)];
    window.DESIGNER_PRINT_PREVIEW = false; showToolpath = false;
    ms.sort((a, b) => a - b);
    return { median_ms: Math.round((ms[1] + ms[2]) / 2), max_ms: Math.round(ms[3]) };
  })()`);
  row({ step: 'Designer: Wall Group thickness edit → envelope shown', ...await designerEdit('envelope') });
  row({ step: 'Designer: same edit, previous full preview (Wall Systems + lattice + route)', ...await designerEdit('preview') });
  row({ step: 'open → Assembly (cold resolve)', ...pick(await evaluate(`__step(() => { setWorkspace('assembly'); return asmResolve(); })`)) });
  row({ step: 'warm resolve (unchanged)', ...pick(await evaluate(`__step(() => asmResolve())`)) });
  row({ step: 'Assembly view redraw', median_ms: await evaluate(`__redraw(() => asmDrawPreview())`) });
  row({ step: 'Assembly panels + view render', median_ms: await evaluate(`__redraw(() => asmRender())`) });
  row({ step: 'opening width', ...pick(await evaluate(`__step(() => asmProfileSet('p1', 'width', 14))`)) });
  row({ step: 'array count', ...pick(await evaluate(`__step(() => asmProfileSetArray('p5', 'count', 3))`)) });
  row({ step: 'array spacing', ...pick(await evaluate(`__step(() => asmProfileSetArray('p5', 'h_spacing', 36))`)) });
  row({ step: 'assembly transform', ...pick(await evaluate(`__step(() => asmSetTransform('asm', 'dx', 0.0625))`)) });
  row({ step: 'planar cut slope', ...pick(await evaluate(`__step(() => asmPlaneSet('p2', 'angle', 14))`)) });
  row({ step: 'max overhang', ...pick(await evaluate(`__step(() => asmSetMaxOverhang(1.5))`)) });
  // rapid edits: five slope changes in a row — coalesced, only the last resolves
  row({ step: '5 rapid plane edits (coalesced)', ...pick(await evaluate(`__step(() => { let p; for (const s of [14.5, 15, 15.5, 16, 16.5]) p = asmPlaneSet('p2', 'angle', s); return p; })`)) });
  row({ step: 'undo', ...pick(await evaluate(`__step(() => asmUndo())`)) });
  row({ step: 'redo', ...pick(await evaluate(`__step(() => asmRedo())`)) });

  // WALL SYSTEMS workspace: several construction edits (nothing generated),
  // GENERATE WALL SYSTEMS (cold), switching generated layers
  row({ step: 'Wall Systems shown (Base envelope)', ...await evaluate(`(async () => {
    const t0 = performance.now(); setWorkspace('wallsystems'); await wsysShow();
    return { ms: Math.round(performance.now() - t0), status: wsysStatus() };
  })()`) });
  row({ step: 'Wall Systems: 6 construction / parameter edits', ...await evaluate(`(async () => {
    const n0 = performance.getEntriesByType ? 0 : 0;
    const L = window.Designer.wallSystems(), t0 = performance.now();
    const wv = L.find(s => s.type === 'interleaved'), pl = L.find(s => s.type === 'parallel'), sk = L.find(s => s.type === 'skin_web');
    wsysSetParam(wv.id, 'paths', 6); wsysSetParam(wv.id, 'period', 26); wsysSetParam(pl.id, 'walls', 4);
    wsysRename(sk.id, 'Rooms web'); wsysSetType(pl.id, 'linked'); wsysSetType(pl.id, 'parallel');
    const sync_ms = performance.now() - t0;
    await wsysNoteState();
    return { sync_ms: +sync_ms.toFixed(1), status: wsysStatus(), generated: false };
  })()`) });
  row({ step: 'Generate Wall Systems (cold, 84 layers)', ...await evaluate(`(async () => {
    const t0 = performance.now(); await wsysGenerate();
    return { ms: Math.round(performance.now() - t0), status: wsysStatus(), layers: wsys.gen && wsys.gen.layers.length,
             bead_findings: wsys.gen && wsys.gen.support.findings.length, error: wsys.error };
  })()`) });
  row({ step: 'switch generated layers (10 new layers, then revisits)', ...await evaluate(`(async () => {
    wsysSetDisplay('generated');
    const ts = [];
    for (let i = 40; i < 50; i++) { const t0 = performance.now(); wsysSelectLayer(i); await _wsysFetchLayer(i); wsysDraw(); ts.push(performance.now() - t0); }
    const t1 = performance.now(); for (let i = 40; i < 50; i++) { wsysSelectLayer(i); wsysDraw(); }
    ts.sort((a, b) => a - b);
    return { new_layer_median_ms: Math.round(ts[5]), new_layer_max_ms: Math.round(ts[9]),
             revisit_ms_per_layer: +((performance.now() - t1) / 10).toFixed(1) };
  })()`) });
  // the Print Simulation: plans from the CURRENT generated geometry only
  row({ step: 'Simulation shown (no plan)', ...await evaluate(`(async () => {
    const t0 = performance.now();
    setWorkspace('simulation'); await simShow();
    return { ms: performance.now() - t0, data: !!sim.data, loading: sim.loading };
  })()`) });
  const simInit = await evaluate(`(async () => {
    const t0 = performance.now();
    const p = simGenerate();
    const sync_ms = performance.now() - t0;
    await p;
    await new Promise(ok => { const w = () => (sim.data && !sim.loading) ? ok() : setTimeout(w, 50); w(); });
    return { sync_ms, ready_ms: performance.now() - t0, error: sim.error, blocked: sim.wsBlocked,
             items: sim.data && sim.data.N, layers: sim.data && sim.data.res.plan.layers.length };
  })()`);
  row({ step: 'Print Simulation start: Plan Print from the generated Wall Systems', ...simInit });
  row({ step: 'switch → Assembly → Simulation (no re-plan)', ...await evaluate(`(async () => {
    const t0 = performance.now();
    setWorkspace('assembly'); await __idle();
    setWorkspace('simulation');
    await new Promise(ok => { const w = () => (sim.data && !sim.loading) ? ok() : setTimeout(w, 20); w(); });
    return { ms: performance.now() - t0, inflight: !!sim.inflight };
  })()`) });
  row({ step: 'print speed change (re-time only)', ...await evaluate(`(async () => {
    const t0 = performance.now();
    await asmSetSpeed('print', 3.5); await simRefresh();
    await new Promise(ok => { const w = () => (sim.data && !sim.loading) ? ok() : setTimeout(w, 20); w(); });
    return { ms: performance.now() - t0 };
  })()`) });
  row({ step: 'opening width edit → Wall Systems outdated', ...await evaluate(`(async () => {
    setWorkspace('assembly');
    const r = await __step(() => asmProfileSet('p1', 'width', 13));
    await wsysNoteState();
    return { exact_ms: Math.round(r.exact_ms), wall_systems: wsysStatus() };
  })()`) });
  row({ step: 'regenerate Wall Systems + an Assembly edit meanwhile', ...await evaluate(`(async () => {
    const t0 = performance.now();
    const p = wsysGenerate();
    await new Promise(ok => setTimeout(ok, 500));
    const r = await __step(() => asmProfileSet('p1', 'width', 12));
    const generating_during_edit = wsysStatus() === 'generating';
    await p;
    await wsysNoteState();
    return { edit_exact_ms: Math.round(r.exact_ms), generating_during_edit, regenerate_ms: Math.round(performance.now() - t0),
             after: wsysStatus() };
  })()`) });
  ws.close();
  return rows;
}

main().catch(e => { console.error(e); process.exit(1); });

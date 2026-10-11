// LAYOUT regression (2026-10-11 correction): the Assembly's right column
// (Assembly Transform + Profiles) never needs horizontal scrolling — footprint
// mode, the assembly-wide and section transforms open, multiple centres — and
// the 3D preview keeps its width. The real page in headless Chrome (DevTools
// protocol); run by tests/test_wall_systems_corrections.py.
// usage: node assembly_panel_layout.js <page url> <project file> <devtools port>
const [, , URL_, PROJECT, PORT = '9333'] = process.argv;
const fs = require('fs');
(async () => {
  const page = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(ok => ws.addEventListener('open', ok));
  let seq = 0; const pending = {};
  ws.addEventListener('message', ev => { const m = JSON.parse(ev.data); if (m.id && pending[m.id]) { pending[m.id](m); delete pending[m.id]; } });
  const send = (method, params = {}) => new Promise(ok => { const id = ++seq; pending[id] = ok; ws.send(JSON.stringify({ id, method, params })); });
  const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true, timeout: 600000 });
    if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 600)); return r.result.result.value; };
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.enable'); await send('Page.navigate', { url: URL_ }); await new Promise(ok => setTimeout(ok, 3000));
  await ev(`new Promise(ok => { const w = () => (typeof openProjectText === 'function') ? ok() : setTimeout(w, 100); w(); })`);
  await ev(`openProjectText(${JSON.stringify(fs.readFileSync(PROJECT, 'utf8'))}, 'b.muddesign')`);
  const out = await ev(`(async () => {
    setWorkspace('assembly'); await asmResolve();
    const measure = (label) => {
      const col = document.getElementById('asm-col-right'), sc = document.getElementById('asm-pane-right');
      const r = col.getBoundingClientRect();
      const over = [...col.querySelectorAll('*')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > r.right + 0.5; })
        .slice(0, 8).map(e => (e.tagName + '.' + e.className + '#' + e.id + ' ' + (e.textContent || '').slice(0, 30) + ' w=' + Math.round(e.getBoundingClientRect().width)));
      const cv = document.getElementById('asm-canvas').getBoundingClientRect();
      return { label, colW: Math.round(r.width), scrollW: sc.scrollWidth, clientW: sc.clientWidth, canvasW: Math.round(cv.width), over };
    };
    const out = [measure('footprint')];
    asmToggleTransform('asm'); out.push(measure('assembly transform open'));
    asm.open[0] = true; asm.open[1] = true; asmRender(); out.push(measure('section transforms open'));
    await asmSetCenterMode('multiple'); await asmAddCenter(); await asmResolve();
    asmRender(); out.push(measure('multiple + centre'));
    await asmSetCenterMode('footprint'); asmToggleTransform('asm'); asmRender();
    return out;
  })()`);
  console.log(JSON.stringify(out));
  ws.close();
})().catch(e => { console.error(e); process.exit(1); });

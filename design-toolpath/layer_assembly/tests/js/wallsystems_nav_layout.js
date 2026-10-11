// LAYOUT + INTERACTION regression (2026-10-11): the Wall Systems workspace's
// layer navigation (◀ ▶, number, slider, Base design, Top View, 3D orbit,
// Envelope / Generated Paths) is in view and clickable at 1600×1000,
// 1280×720 and 1024×640 without page scrolling; Next and the slider select
// layers; the generated geometry of the selected layer is shown. The real page
// in headless Chrome; run by tests/test_printability_inspection.py.
// usage: node wallsystems_nav_layout.js <page url> <project file> <devtools port>
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
  const sizes = [[1600, 1000], [1280, 720], [1024, 640]];
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.enable'); await send('Page.navigate', { url: URL_ }); await new Promise(ok => setTimeout(ok, 3000));
  await ev(`new Promise(ok => { const w = () => (typeof openProjectText === 'function') ? ok() : setTimeout(w, 100); w(); })`);
  await ev(`openProjectText(${JSON.stringify(fs.readFileSync(PROJECT, 'utf8'))}, 'b.muddesign')`);
  await ev(`(async () => { setWorkspace('wallsystems'); await wsysShow(); await wsysGenerate(); await new Promise(ok => setTimeout(ok, 300)); })()`);
  const out = [];
  for (const [w, h] of sizes) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    out.push(await ev(`(async () => {
      await new Promise(ok => setTimeout(ok, 200));
      for (let k = 0; k < 30; k++) { wsysDraw(); await new Promise(ok => requestAnimationFrame(ok)); }
      const ids = ['wsys-prev', 'wsys-next', 'wsys-layer-input', 'wsys-layer-slider', 'wsys-base-btn', 'wsys-top-btn', 'wsys-orbit-btn', 'wsys-show-env', 'wsys-show-gen'];
      const vis = ids.map(id => { const e = document.getElementById(id), b = e.getBoundingClientRect();
        const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
        const hit = document.elementFromPoint(cx, cy);
        return { id, inView: b.top >= 0 && b.bottom <= innerHeight && b.left >= 0 && b.right <= innerWidth && b.width > 0,
                 clickable: hit === e || e.contains(hit) }; });
      // interaction through the real controls
      document.getElementById('wsys-next').click(); await new Promise(ok => setTimeout(ok, 50));
      const afterNext = wsys.layer;
      const sl = document.getElementById('wsys-layer-slider'); sl.value = '5'; sl.dispatchEvent(new Event('input'));
      await new Promise(ok => setTimeout(ok, 300));
      const sc = wsysScene();
      return { size: innerWidth + 'x' + innerHeight, docScroll: document.documentElement.scrollHeight > innerHeight,
               bad: vis.filter(v => !v.inView || !v.clickable).map(v => v.id), afterNext, layerAfterSlider: wsys.layer, scene: sc };
    })()`));
  }
  console.log(JSON.stringify(out));
  ws.close();
})().catch(e => { console.error(e); process.exit(1); });

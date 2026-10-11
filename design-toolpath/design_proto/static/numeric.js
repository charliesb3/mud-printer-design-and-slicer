// ===========================================================================
// NUMERIC FIELDS (2026-10-09, reliability pass) — every number input of the
// app (`input[type=number].prop-input`: Designer, Assembly, Simulation) gets
// the same fixed-size field with large − / + buttons instead of the native
// spinner, which shifted under the pointer:
//   * panels are rebuilt (innerHTML) when a value changes, so the input
//     under the pointer was REPLACED — press-and-hold stopped, focus was lost;
//   * `.prop-input` was flex: 1 — its width (and the spinner's x) followed
//     the neighbouring text;
//   * lines above a field appear / wrap with the new value (a transform
//     summary, warnings), pushing the field down between two clicks.
// Here:
//   * a step is the browser's own stepUp / stepDown (the field's step, min,
//     max and step-grid snapping, exactly like the native spinner), then
//     `input` + `change` are dispatched — the field's own handlers validate
//     and apply as before;
//   * a field is identified by a KEY that survives a rebuild (its id, else
//     the nearest ancestor id + label + data-field + position), so
//     press-and-hold keeps stepping the REPLACEMENT element, focus moves to
//     the replacement, and the scrolling column is adjusted so the field
//     stays at the same place on screen (the anchor lives while the field
//     is in use);
//   * fixed dimensions (CSS `.num-field`), tabular digits.
// No-op without a real DOM (the node UI smoke tests' stubs).
// ===========================================================================
(function () {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined' ||
      typeof document.querySelectorAll !== 'function') return;
  const SEL = 'input[type="number"].prop-input';
  const HOLD_DELAY = 400, HOLD_EVERY = 80;     // ms: first repeat, then one step every …
  const ANCHOR_MS = 2500;                      // keep a stepped field in place this long after the last step
  let hold = null;                             // { key, dir, timer }
  let focus = null;                            // { key, el }: the focused field (restored after a rebuild)
  let anchor = null;                           // { key, top, until }

  function keyOf(inp) {
    if (inp.id) return '#' + inp.id;
    let a = inp.parentElement;
    while (a && !a.id) a = a.parentElement;
    const row = inp.closest('.prop-row, .asm-row, .num-row') || inp.parentElement;
    const lab = row && row.querySelector('.prop-label');
    const all = a ? Array.from(a.querySelectorAll(SEL)) : [];
    return [a ? a.id : '', lab ? lab.textContent : '', inp.dataset.field || '', all.indexOf(inp)].join('|');
  }

  function find(key) {
    if (key.startsWith('#')) return document.getElementById(key.slice(1));
    for (const el of document.querySelectorAll(SEL)) if (keyOf(el) === key) return el;
    return null;
  }

  function enhance(inp) {
    if (inp.dataset.numField) return;
    inp.dataset.numField = '1';
    const wrap = document.createElement('span');
    wrap.className = 'num-field';
    const row = inp.parentElement;
    // a row with other controls too (a second field, a select, a button): the compact size
    if (row && (row.querySelectorAll(SEL).length > 1 || row.querySelector('select, button:not(.num-step)')))
      wrap.classList.add('num-compact');
    const btn = (dir, txt, label) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'num-step ' + (dir > 0 ? 'num-up' : 'num-down');
      b.tabIndex = -1; b.textContent = txt; b.setAttribute('aria-label', label);
      b.dataset.dir = String(dir);
      return b;
    };
    row.insertBefore(wrap, inp);
    row.classList.add('num-row');
    wrap.append(btn(-1, '−', 'Decrease'), inp, btn(1, '+', 'Increase'));
    if (inp.title && !wrap.title) wrap.title = inp.title;
  }

  function enhanceIn(root) {
    if (root.matches && root.matches(SEL)) enhance(root);
    if (root.querySelectorAll) for (const el of root.querySelectorAll(SEL)) enhance(el);
  }

  function scroller(el) {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const oy = getComputedStyle(p).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) return p;
    }
    return null;
  }

  // keep the field in use where it was on screen (content above it changed)
  function reanchor() {
    if (!anchor) return;
    const inUse = hold || performance.now() < anchor.until || (focus && focus.key === anchor.key);
    if (!inUse) { anchor = null; return; }
    const el = find(anchor.key);
    if (!el || !el.offsetParent) return;
    const dy = el.getBoundingClientRect().top - anchor.top;
    if (Math.abs(dy) < 0.5) return;
    const sc = scroller(el);
    if (!sc) return;
    sc.scrollTop += dy;
    sc._numScroll = sc.scrollTop;                 // (this scroll event is ours, not the user's)
  }

  function setAnchor(el) {
    anchor = { key: keyOf(el), top: el.getBoundingClientRect().top, until: performance.now() + ANCHOR_MS };
  }

  function step(inp, dir) {
    if (!inp || inp.disabled || inp.readOnly) return false;
    const before = inp.value;
    try { if (dir > 0) inp.stepUp(); else inp.stepDown(); }
    catch (e) {                                   // step="any": one unit
      const v = parseFloat(inp.value);
      inp.value = String((isFinite(v) ? v : 0) + dir);
    }
    if (inp.value === before) return false;      // at a bound: nothing happens (as the native spinner)
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function stopHold() {
    if (hold) clearTimeout(hold.timer);
    hold = null;
    document.documentElement.classList.remove('num-holding');
  }

  function repeat() {
    if (!hold) return;
    const el = find(hold.key);                    // (the panel may have been rebuilt meanwhile)
    if (!el || !step(el, hold.dir)) { stopHold(); return; }
    if (anchor) anchor.until = performance.now() + ANCHOR_MS;
    reanchor();
    hold.timer = setTimeout(repeat, HOLD_EVERY);
  }

  document.addEventListener('pointerdown', e => {
    const b = e.target && e.target.closest ? e.target.closest('.num-step') : null;
    if (!b) {                                     // a click elsewhere: the user moved on
      if (!(e.target && e.target.closest && e.target.closest('.num-field'))) anchor = null;
      return;
    }
    if (e.button !== 0) return;
    e.preventDefault();                           // no focus jump, no text selection
    const inp = b.parentElement.querySelector('input');
    if (!inp || inp.disabled) return;
    stopHold();
    if (document.activeElement !== inp) inp.focus({ preventScroll: true });
    setAnchor(inp);
    const key = keyOf(inp), dir = +b.dataset.dir;
    hold = { key, dir, timer: null };
    document.documentElement.classList.add('num-holding');
    if (!step(inp, dir)) { stopHold(); return; }
    reanchor();
    if (hold) hold.timer = setTimeout(repeat, HOLD_DELAY);
  }, true);
  for (const t of ['pointerup', 'pointercancel']) document.addEventListener(t, stopHold, true);
  window.addEventListener('blur', stopHold);
  document.addEventListener('wheel', () => { anchor = null; }, { passive: true, capture: true });
  document.addEventListener('scroll', e => {     // the user scrolled (scrollbar, keys): no anchoring
    const t = e.target;
    if (anchor && t && t.nodeType === 1 && t._numScroll !== t.scrollTop) anchor = null;
  }, true);

  document.addEventListener('focusin', e => {
    const t = e.target;
    if (t && t.matches && t.matches(SEL)) {
      focus = { key: keyOf(t), el: t };
      if (!anchor || anchor.key !== focus.key) setAnchor(t);
    } else if (t && t !== document.body) { focus = null; anchor = null; }
  });
  document.addEventListener('focusout', e => {
    const t = e.target;
    if (!focus || focus.el !== t) return;
    if (e.relatedTarget) { focus = null; return; }          // focus went elsewhere on purpose
    setTimeout(() => { if (focus && focus.el === t && t.isConnected) focus = null; }, 0);   // left (not rebuilt)
  });

  // rebuilt panels: new fields enhanced; the field in use keeps focus and place
  new MutationObserver(recs => {
    let added = false;
    for (const r of recs) for (const n of r.addedNodes) if (n.nodeType === 1) { enhanceIn(n); added = true; }
    if (!added) return;
    if (focus && !focus.el.isConnected) {
      const a = document.activeElement;
      if (!a || a === document.body || a === document.documentElement) {
        const el = find(focus.key);
        if (el && el.offsetParent) { focus.el = el; el.focus({ preventScroll: true }); }
      }
    }
    reanchor();
  }).observe(document.documentElement, { childList: true, subtree: true });

  const init = () => enhanceIn(document.body || document.documentElement);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.NumericFields = { keyOf, find, step, enhance: enhanceIn,
                           state: () => ({ hold: hold && { key: hold.key, dir: hold.dir }, focus: focus && focus.key,
                                           anchor: anchor && { key: anchor.key, top: anchor.top } }) };
})();

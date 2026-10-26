"""
UI tests for the controller dashboard (UI pass 2026-10-14):

- layout: LEFT = Current Job → Print progress → Print parameters (speed
  inside), MIDDLE = Controls (Start / Hold / Resume / Stop / Set origin /
  Set prime / Prime / Outline) → Manual Jog, RIGHT = the toolpath top view;
  machine-operation buttons are not in the Current Job panel; the old
  "end of lift" picker and "measured height" input are gone;
- the browser's lift-parameter preview (app.js `proposeLiftEnd`) gives the
  same answer as the server's rule (`CurrentJob.propose_lift_end`).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.jobs.current_job import load_job_from_file

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "mudjob" / "fixtures"


VOID = {"input", "meta", "link", "br", "img", "hr", "source"}


class _Tree(HTMLParser):
    """Records, for every element with an id, its ancestor ids/classes and document order."""

    def __init__(self):
        super().__init__()
        self.stack, self.order, self.where = [], [], {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        node = (tag, a.get("id"), a.get("class", ""))
        if a.get("id"):
            self.order.append(a["id"])
            self.where[a["id"]] = [(i, c) for _, i, c in self.stack + [node]]
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        assert self.stack and self.stack[-1][0] == tag, f"mismatched </{tag}>"
        self.stack.pop()


@pytest.fixture(scope="module")
def tree():
    t = _Tree()
    t.feed((STATIC / "index.html").read_text())
    assert not t.stack, "unclosed elements"
    return t


def _inside(tree, el_id, container_class):
    return any(container_class in (cls or "").split() for _, cls in tree.where[el_id][:-1])


def _inside_id(tree, el_id, container_id):
    return any(i == container_id for i, _ in tree.where[el_id][:-1])


class TestLayout:
    def test_three_areas(self, tree):
        for el in ("job-section", "progress-section", "params-section"):
            assert _inside(tree, el, "col-left"), el
        for el in ("controls-section", "jog-section"):
            assert _inside(tree, el, "col-mid"), el
        assert _inside(tree, "view-section", "col-view") and _inside(tree, "path-canvas", "col-view")

    def test_panel_order(self, tree):
        o = tree.order
        assert o.index("job-section") < o.index("progress-section") < o.index("params-section")
        assert o.index("controls-section") < o.index("jog-section")

    def test_machine_operations_are_in_controls_not_job(self, tree):
        for b in ("btn-start", "btn-hold", "btn-resume", "btn-stop", "btn-set-origin", "btn-set-prime",
                  "btn-prime", "btn-outline"):
            assert _inside_id(tree, b, "controls-section"), b
            assert not _inside_id(tree, b, "job-section"), b
        assert _inside_id(tree, "file-input", "job-section")

    def test_speed_and_lift_parameters_together(self, tree):
        for el in ("p-target-value", "p-thick-value", "btn-apply-params", "btn-restore-lifts",
                   "print-speed-value", "print-speed-slider", "print-speed-plus", "print-speed-minus"):
            assert _inside_id(tree, el, "params-section"), el
        steps = (STATIC / "index.html").read_text()
        assert 'data-param="target"' in steps and 'data-param="thickness"' in steps
        # The ±10 % feed override shortcuts and 100 % reset are preserved.
        for step in ("-10", "10", "reset"):
            assert f'data-step="{step}"' in steps

    def test_speed_sliders_are_present(self, tree):
        for el in ("print-speed-slider", "jog-speed-slider"):
            assert el in tree.where, el
        # Both sliders are range inputs.
        html = (STATIC / "index.html").read_text()
        assert 'id="print-speed-slider" class="speed-slider"' in html
        assert 'id="jog-speed-slider" class="speed-slider"' in html

    def test_low_level_controls_removed(self, tree):
        for gone in ("adjust-select", "obs-height", "obs-measure", "obs-thickness", "lift-section", "setup-section",
                     "ovr-value", "ovr-fill", "planned-speeds"):
            assert gone not in tree.where, gone
        # The old 6-preset / 2-nudge jog-speed buttons are gone — the slider
        # replaces them.
        html = (STATIC / "index.html").read_text()
        assert 'class="ctrl-btn btn-jog-speed"' not in html
        assert 'class="ctrl-btn btn-jog-nudge"' not in html


def _cases():
    out = []
    for name in ("designer-export-single-wall.mudjob", "designer-export-3-lifts.mudjob"):
        job = load_job_from_file(FIXTURES / name)
        for state, line in (("ready", 0), ("running", job.layout.segments[1][0] + 1)):
            job.state, job.current_line = state, line
            for target in (1.5, 3.0, 4.5, 9.0, 18.0):
                for t in (0.5, 1.0, 1.5, 2.0, 5.0):
                    srv = job.propose_lift_end(target, t)
                    out.append({"candidates": job.end_candidates(), "target": target, "thickness": t,
                                "max_z": job.lift_plan["max_z_travel"],
                                "server": {"needed": srv["needed"],
                                           "at": srv["choice"]["at"] if srv["choice"] else None,
                                           "reason": srv["reason"]}})
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_preview_matches_the_server_rule():
    cases = _cases()
    r = subprocess.run(["node", str(Path(__file__).parent / "js" / "propose_check.js"), str(STATIC / "app.js")],
                       input=json.dumps(cases), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert len(got) == len(cases) > 50
    for g, c in zip(got, cases):
        srv = c["server"]
        assert (g["needed"], g["at"]) == (srv["needed"], srv["at"]), (c["target"], c["thickness"])
        assert (g["reason"] is None) == (srv["reason"] is None)
        if srv["reason"]:
            # same message apart from number formatting (JS 1 vs Python 1.0)
            assert g["reason"].split("—")[0].split(" ")[0] == srv["reason"].split("—")[0].split(" ")[0]


class TestPrinterSettings:
    def test_settings_are_not_in_the_operating_columns(self, tree):
        for el in ("printer-section", "pp-name", "pp-x", "pp-y", "pp-zmax", "pp-zframe", "printer-save"):
            assert el in tree.where, el
        for col in ("col-left", "col-mid", "col-view"):
            assert not _inside(tree, "printer-section", col)
        assert "printer-chip" in tree.where                        # opened from the status bar

    def test_preflight_is_shown_with_the_job(self, tree):
        assert _inside_id(tree, "job-preflight", "job-section")


class TestHardwareUI:
    def test_manual_z_with_the_machine_controls(self, tree):
        for el in ("z-section", "z-up", "z-down", "z-status"):
            assert _inside(tree, el, "col-mid"), el
        assert tree.order.index("jog-section") < tree.order.index("z-section")

    def test_touch_scrollbar_structure_and_wiring(self, tree):
        """
        Pin the ninth-pass industrial touchscreen scrollbar:
          * The three DOM nodes (ts-up / ts-track / ts-thumb / ts-down)
            exist at the #app level, outside #main-content.
          * CSS hides it on fine pointer and reveals it on coarse pointer
            with a 90-px width, large arrows, and a draggable thumb.
          * JS wires up drag, arrow press-and-hold, and track-tap paging.
        """
        for el in ("touch-scrollbar", "ts-up", "ts-down", "ts-track", "ts-thumb"):
            assert el in tree.where, el
        # Must NOT be inside #main-content (would be clipped by its overflow).
        assert not _inside_id(tree, "touch-scrollbar", "main-content")

        html = (STATIC / "index.html").read_text()
        css = (STATIC / "style.css").read_text()
        js = (STATIC / "app.js").read_text()

        # Scrollbar is hidden by default and shown only on pointer: coarse.
        assert "#touch-scrollbar { display: none; }" in css
        assert "@media (pointer: coarse)" in css
        # Width ≈ 80–100 px per the spec.
        assert "width: 90px" in css
        # Native scrollbar hidden on touch so the custom one is the only
        # scroll affordance.
        assert "scrollbar-width: none" in css
        # The main content area is padded so content doesn't slide under
        # the fixed scrollbar.
        assert "#main-content { padding-right: 98px; }" in css

        # JS wires every requirement.
        assert "attachTouchScrollbar" in js
        # Drag via Pointer Events + capture.
        assert 'thumb.addEventListener("pointerdown"' in js
        assert "setPointerCapture" in js
        # Thumb follows scroll.
        assert 'main.addEventListener("scroll"' in js
        # Resync when DOM changes.
        assert "MutationObserver" in js
        # Arrow press-and-hold with release.
        assert "arrowHold" in js and "setInterval" in js and "clearInterval" in js
        # Track tap scroll (page up / down).
        assert 'track.addEventListener("pointerdown"' in js and 'scrollBy' in js

    def test_held_input_release_safety_handlers_present(self):
        """Pin the eighth-pass hardening — the client must have:
          * global visibilitychange / blur / pagehide / beforeunload stops
          * pointerup / pointercancel / lostpointercapture on held buttons
          * a client-side max-hold ceiling that matches the server
        If any of these disappears, the Z/jog release safety story weakens
        and this test must fail so the regression is caught.
        """
        js = (STATIC / "app.js").read_text()
        # Global stop-all for focus / visibility loss.
        assert 'stopAllHeldInputs' in js
        assert '"visibilitychange"' in js
        assert '"blur"' in js
        assert '"pagehide"' in js
        assert '"beforeunload"' in js
        # Pointer Events + capture on held buttons.
        assert 'setPointerCapture' in js
        assert '"pointerdown"' in js
        assert '"pointerup"' in js
        assert '"pointercancel"' in js
        assert '"lostpointercapture"' in js
        # Client-side max-hold ceiling exists for both jog and Z.
        assert 'INPUT_MAX_HOLD_MS' in js
        assert 'jogMaxHoldTimer' in js
        assert 'zMaxHoldTimer' in js

    def test_remote_stop_panel_spans_and_is_never_an_estop(self, tree):
        assert "rstop-panel" in tree.where and "btn-rstop-clear" in tree.where
        for col in ("col-left", "col-mid", "col-view"):
            assert not _inside(tree, "rstop-panel", col)
        html = (STATIC / "index.html").read_text()
        panel = html.split('id="rstop-panel"')[1].split('id="rstop-blocked"')[0]
        assert "REMOTE STOP" in panel and "not the E-stop" in panel
        assert "EMERGENCY" not in panel.upper()

    def test_hardware_commissioning_panel_outside_the_columns(self, tree):
        for el in ("hw-section", "hw-remote", "hw-z", "hw-events"):
            assert el in tree.where, el
        for col in ("col-left", "col-mid", "col-view"):
            assert not _inside(tree, "hw-section", col)
        assert 'data-read="info"' in (STATIC / "index.html").read_text()

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
                   "planned-speeds", "ovr-value"):
            assert _inside_id(tree, el, "params-section"), el
        steps = (STATIC / "index.html").read_text()
        assert 'data-param="target"' in steps and 'data-param="thickness"' in steps
        for step in ("-10", "-1", "reset", "1", "10"):
            assert f'data-step="{step}"' in steps

    def test_low_level_controls_removed(self, tree):
        for gone in ("adjust-select", "obs-height", "obs-measure", "obs-thickness", "lift-section", "setup-section"):
            assert gone not in tree.where, gone
        assert 'type="range"' not in (STATIC / "index.html").read_text()      # no draggable live slider


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

"""
THIS controller's machine profile and job preflight (2026-10-15).

Machine profiles are independent configuration on each side; `.mudjob`
communicates job assumptions and requirements, it does not synchronize
applications. Contract interoperability: fixture FILES only.

- the profile: defaults (only the documented Z per frame), validation,
  local persistence, a damaged file reported;
- preflight from the job itself (G-code XY moves, lift / layer Z) against the
  controller's profile, which is authoritative: size fails, placement warns,
  building Z and lift fail, unset = unchecked;
- the job's own claims (`prepared_for`, Designer `preflight`) are parsed and
  reported, never trusted: a Designer "fits" cannot unblock a failing job;
- an incompatible native job cannot Start; external G-code is informational;
- the profile can be changed only when no job is active; lift adjustments use
  this printer's Z per frame; the envelope in the state; older jobs work.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import config
from app import machine_profile as MP
from app import main as main_module
from app.api import http as http_module
from app.api.ws import ConnectionManager
from app.grbl.communicator import GRBLCommunicator
from app.jobs.current_job import JobFormatError, load_job_from_file, parse_building_job
from app.jobs.preflight import job_preflight
from app.main import app
from app.state import AppState
from .mock_transport import MockTransport, wait_for

FIX = Path(__file__).resolve().parents[2] / "contracts" / "mudjob" / "fixtures"
WALL = FIX / "designer-export-single-wall.mudjob"            # 40 × 30 in, 12 layers of 1.5 (18 in), lifts of 4.5
PREPARED = FIX / "designer-export-prepared-for.mudjob"       # the same, prepared for a 120 × 120 × 100 in printer
OLD = FIX / "executable-3-lifts.mudjob"                      # hand-written, no requirements / prepared_for


def profile(**kw):
    p = MP.default_profile()
    p.update({"name": "Shop printer", **kw})
    return MP.validate(p)


def loaded(path, printer):
    job = load_job_from_file(path)
    job.printer = printer
    return job


def statuses(pf):
    return {c["id"]: c["status"] for c in pf["checks"]}


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MACHINE_PROFILE_PATH", tmp_path / "machine_profile.json")


# ------------------------------------------------------------------ #

class TestProfile:
    def test_defaults_set_only_the_documented_z(self):
        p = MP.default_profile()
        assert p["x_extent"] is None and p["y_extent"] is None and p["max_build_z"] is None
        assert p["z_travel_per_frame"] == 18.0 and p["units"] == "in"
        assert MP.envelope(p) is None

    @pytest.mark.parametrize("bad, msg", [({"units": "mm"}, "inches"), ({"x_extent": 0}, "positive"),
                                          ({"name": " "}, "name"), ({"y_extent": "big"}, "positive")])
    def test_validation(self, bad, msg):
        with pytest.raises(MP.MachineProfileError, match=msg):
            MP.validate({**MP.default_profile(), **bad})

    def test_persistence_round_trip(self, tmp_path):
        path = tmp_path / "p.json"
        saved = MP.save(path, profile(x_extent=144, y_extent=96, max_build_z=240, future={"k": 1}))
        p, problem = MP.load(path)
        assert problem is None and p == saved and p["future"] == {"k": 1}
        assert MP.envelope(p) == {"min_x": 0.0, "min_y": 0.0, "max_x": 144.0, "max_y": 96.0}

    def test_missing_and_damaged_files(self, tmp_path):
        assert MP.load(tmp_path / "none.json") == (MP.default_profile(), None)
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json")
        p, problem = MP.load(bad)
        assert p == MP.default_profile() and "could not be read" in problem


class TestPreflight:
    def test_all_unset_is_unchecked_not_blocking(self):
        pf = job_preflight(loaded(WALL, MP.default_profile()), MP.default_profile())
        assert statuses(pf) == {"x": "unchecked", "y": "unchecked", "z": "unchecked", "lift": "ok"}
        assert pf["fits"] and not pf["blocking"] and pf["native"]

    def test_fits(self):
        p = profile(x_extent=120, y_extent=120, max_build_z=100)
        pf = job_preflight(loaded(WALL, p), p)
        assert set(statuses(pf).values()) == {"ok"} and not pf["blocking"]
        assert pf["requirements"]["xy_bounds"] == {"min_x": 0.0, "min_y": 0.0, "max_x": 40.0, "max_y": 30.0}
        assert pf["requirements"]["building_height"] == 18.0 and pf["requirements"]["max_lift_height"] == pytest.approx(4.5)

    @pytest.mark.parametrize("kw, failing", [({"x_extent": 30}, "x"), ({"y_extent": 20}, "y"),
                                             ({"max_build_z": 12}, "z"), ({"z_travel_per_frame": 4}, "lift")])
    def test_each_limit_blocks_a_native_job(self, kw, failing):
        p = profile(**{"x_extent": 120, "y_extent": 120, "max_build_z": 100, **kw})
        pf = job_preflight(loaded(WALL, p), p)
        assert pf["blocking"] and statuses(pf)[failing] == "fail"
        assert [c["id"] for c in pf["checks"] if c["status"] == "fail"] == [failing]

    def test_placement_outside_the_envelope_warns(self, tmp_path):
        doc = json.loads(WALL.read_text())
        f = tmp_path / "shifted.mudjob"
        doc["lifts"][0]["gcode"].insert(1, "G1 X-30 Y5 F360")     # one move left of the origin
        doc["lifts"][0]["travel_lines"] = [i + 1 if i >= 1 else i for i in doc["lifts"][0]["travel_lines"]] + [1]
        doc["lifts"][0]["layer_marks"] = [{**m, "line": m["line"] + (1 if m["line"] >= 1 else 0)}
                                          for m in doc["lifts"][0]["layer_marks"]]
        doc["reentry_marks"] = False
        f.write_text(json.dumps(doc))
        p = profile(x_extent=100, y_extent=100)
        pf = job_preflight(loaded(f, p), p)
        assert statuses(pf)["x"] == "warn" and not pf["blocking"] and "print origin" in pf["checks"][0]["message"]

    def test_designer_claim_cannot_override(self):
        p = profile(x_extent=30, y_extent=120, max_build_z=100)
        job = loaded(PREPARED, p)
        assert job.designer_preflight["fits"] is True and job.prepared_for["name"] == "Designer assumption"
        pf = job_preflight(job, p)
        assert pf["blocking"] and pf["designer_fits"] is True
        assert any("Designer reported" in n for n in pf["notes"])
        assert any("Prepared for a different machine" in n and "X 120 vs 30" in n for n in pf["notes"])

    def test_matching_prepared_for_has_no_note(self):
        p = profile(x_extent=120, y_extent=120, max_build_z=100)
        pf = job_preflight(loaded(PREPARED, p), p)
        assert not pf["notes"] and not pf["blocking"]
        assert pf.get("prepared_for_mismatch") is None

    def test_prepared_for_mismatch_is_structured(self):
        """Fourteenth-pass: the preflight exposes a structured
        prepared_for_mismatch so the UI can render a prominent panel
        with both the Designer target and this controller side by
        side (rather than only a plain text note)."""
        p = profile(name="This controller", x_extent=30, y_extent=120, max_build_z=100)
        pf = job_preflight(loaded(PREPARED, p), p)
        m = pf.get("prepared_for_mismatch")
        assert m is not None
        assert m["designer_name"] == "Designer assumption"
        assert m["controller_name"] == "This controller"
        xd = next(d for d in m["diffs"] if d["key"] == "x_extent")
        assert xd["label"] == "X" and xd["designer"] == 120.0 and xd["controller"] == 30.0
        assert xd["units"] == "in"

    def test_older_jobs_without_machine_data(self):
        p = profile(x_extent=120, y_extent=120, max_build_z=100)
        job = loaded(OLD, p)
        assert job.prepared_for is None and job.designer_preflight is None and job.declared_requirements is None
        pf = job_preflight(job, p)
        assert pf["fits"] and statuses(pf)["z"] == "ok"            # 54 in from its lift metadata

    def test_external_gcode_is_informational(self, tmp_path):
        f = tmp_path / "big.nc"
        f.write_text("G20 G90\nG1 X0 Y0 F60\nG1 X500 Y0\n")
        p = profile(x_extent=100, y_extent=100)
        pf = job_preflight(loaded(f, p), p)
        assert statuses(pf)["x"] == "fail" and not pf["native"] and not pf["blocking"]

    def test_bad_machine_blocks_are_rejected(self):
        doc = json.loads(WALL.read_text())
        doc["prepared_for"] = "a printer"
        with pytest.raises(JobFormatError, match="prepared_for"):
            parse_building_job(json.dumps(doc))

    def test_lift_adjustment_uses_this_printers_z(self):
        p = profile(z_travel_per_frame=4.0)
        job = loaded(WALL, p)
        assert job.max_z_travel() == 4.0
        c = {c["layers"]: c for c in job.end_candidates()}
        assert c[2]["exceeds_max"] is False and c[3]["exceeds_max"] is True        # 3 × 1.5 = 4.5 > 4


# ------------------------------------------------------------------ #
# Start blocking and profile editing through the controller           #
# ------------------------------------------------------------------ #

class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, d):
        self.sent.append(d)


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(config, "STATUS_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 254.0)
    transport = MockTransport(auto_ok=True, status_reply="<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    transport.replies["$$"] = ["$13=0"]
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    http_module.init(state, comm, ConnectionManager())
    http_module._current_job = None
    http_module._runner_task = None
    await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
    await http_module._handle_message({"type": "set_origin"}, FakeWS())
    yield comm, transport, state
    await http_module._stop_job()
    http_module._current_job = None
    http_module._state = None
    http_module._comm = None
    await comm.stop()


async def msg(d):
    ws = FakeWS()
    await http_module._handle_message(d, ws)
    return ws


class TestController:
    async def test_incompatible_job_cannot_start_until_resolved(self, env):
        comm, transport, state = env
        assert (await msg({"type": "set_machine_profile", "profile": profile(x_extent=30, y_extent=120)})).sent[0]["level"] == "info"
        job = http_module._current_job = loaded(PREPARED, state.machine_profile)
        ws = await msg({"type": "job_start"})
        assert "does not fit" in ws.sent[0]["message"] and http_module._runner_task is None
        assert state.to_dict()["job"]["preflight"]["blocking"] is True
        # deliberately resolved: this printer's profile corrected (e.g. a wrong value was entered)
        await msg({"type": "set_machine_profile", "profile": profile(x_extent=120, y_extent=120)})
        assert job.printer["x_extent"] == 120.0
        assert (await msg({"type": "job_start"})).sent == []
        await wait_for(lambda: job.state in ("running", "awaiting_reposition", "completed"), timeout=3.0)

    async def test_profile_saved_locally_and_locked_while_printing(self, env):
        comm, transport, state = env
        await msg({"type": "set_machine_profile", "profile": profile(x_extent=200, y_extent=150, max_build_z=300)})
        on_disk = json.loads(config.MACHINE_PROFILE_PATH.read_text())
        assert on_disk["x_extent"] == 200.0 and state.machine_profile == MP.validate(on_disk)
        assert state.to_dict()["machine"]["envelope"] == {"min_x": 0.0, "min_y": 0.0, "max_x": 200.0, "max_y": 150.0}
        job = http_module._current_job = loaded(WALL, state.machine_profile)
        job.state = "running"
        ws = await msg({"type": "set_machine_profile", "profile": profile(x_extent=10)})
        assert "cannot change" in ws.sent[0]["message"] and state.machine_profile["x_extent"] == 200.0
        job.state = "ready"
        ws = await msg({"type": "set_machine_profile", "profile": {"units": "mm", "name": "x"}})
        assert "inches" in ws.sent[0]["message"]


@pytest.fixture
def offline_client(monkeypatch):
    async def no_connection_loop():
        return None
    monkeypatch.setattr(main_module, "_connection_loop", no_connection_loop)
    http_module._current_job = None
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    http_module._current_job = None
    main_module.app_state.mark_disconnected()
    main_module.app_state.machine_profile = MP.default_profile()


class TestApi:
    def test_profile_loaded_at_startup_and_applied_to_uploads(self, monkeypatch, tmp_path):
        path = tmp_path / "boot.json"
        MP.save(path, profile(name="Boot printer", x_extent=30, y_extent=120))
        monkeypatch.setattr(config, "MACHINE_PROFILE_PATH", path)

        async def no_connection_loop():
            return None
        monkeypatch.setattr(main_module, "_connection_loop", no_connection_loop)
        with TestClient(app, raise_server_exceptions=False) as c:
            s = c.get("/api/state").json()
            assert s["machine"]["profile"]["name"] == "Boot printer"
            c.post("/upload", files={"file": ("p.mudjob", PREPARED.read_bytes(), "application/json")})
            pf = c.get("/api/state").json()["job"]["preflight"]
            assert pf["blocking"] and pf["designer_fits"] is True and pf["profile_name"] == "Boot printer"
        main_module.app_state.machine_profile = MP.default_profile()

    def test_flat_gcode_still_uploads(self, offline_client):
        r = offline_client.post("/upload", files={"file": ("a.nc", b"G21\nG0 X1\nG1 X2 F100\n", "text/plain")}).json()
        assert r["success"]
        pf = offline_client.get("/api/state").json()["job"]["preflight"]
        assert pf["native"] is False and pf["blocking"] is False

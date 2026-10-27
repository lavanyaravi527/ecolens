import json
import os

import pytest
from fastapi.testclient import TestClient

from backend import app as appmod
from backend.calibrate import profile
from .helpers import needs_cc


@pytest.fixture
def client(data_dir):
    return TestClient(appmod.app)


def test_profile_is_404_until_calibrated(client):
    assert client.get("/api/profile").status_code == 404
    assert client.get("/api/health").json()["profile_exists"] is False


def test_saved_profile_is_served_without_the_raw_data(client):
    profile.save_profile({"schema": 1, "weights_pj": {"alu": 1}, "raw": {"big": [1] * 1000}})
    r = client.get("/api/profile").json()
    assert r["weights_pj"] == {"alu": 1} and "raw" not in r


def test_measure_rejects_injection(client):
    bad = {"body": [{"t": "asg", "n": "x;system(0)", "e": ["num", 1]}]}
    r = client.post("/api/measure", json={"before": bad})
    assert r.status_code == 422 and "Cannot translate" in r.json()["detail"]


def test_measure_validates_parameters(client):
    r = client.post("/api/measure", json={"before": {"body": []}, "reps": 1000})
    assert r.status_code == 422


@needs_cc
def test_measure_endpoint_end_to_end(client, programs):
    d = programs["demo"]
    r = client.post("/api/measure", json={"before": d["before"], "after": d["after"],
                                          "reps": 2, "target_seconds": 0.05})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["same_result"] and j["before"]["energy_pj"] > j["after"]["energy_pj"]


@needs_cc
@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="set RUN_SLOW=1 (takes ~40 s)")
def test_calibration_end_to_end(client):
    import time
    assert client.post("/api/calibrate", json={"quick": True, "watts": 12}).status_code == 202
    for _ in range(300):
        st = client.get("/api/calibrate/status").json()
        if st["state"] in ("done", "error"):
            break
        time.sleep(1)
    assert st["state"] == "done", st
    p = client.get("/api/profile").json()
    assert p["weights_pj"]["div"] > p["weights_pj"]["alu"] > 0
    assert p["energy_source"] in ("rapl", "time_proxy")

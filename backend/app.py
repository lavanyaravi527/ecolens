"""EcoLens backend API.

    GET  /api/health             what this machine can do (RAPL? compiler?) and whether a profile exists
    GET  /api/profile            the measured hardware profile (404 until calibrated)
    POST /api/calibrate          start a calibration in the background
    GET  /api/calibrate/status   progress of that calibration
    POST /api/measure            compile + run the user's program (before / after fixes) and return real energy

The frontend folder is served at / so one `uvicorn` command runs everything.
"""
from __future__ import annotations

import os
import threading
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .calibrate import energy, profile, runner
from .measure import service

app = FastAPI(title="EcoLens backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ECOLENS_CORS", "http://localhost:8000,http://127.0.0.1:8000").split(","),
    allow_methods=["GET", "POST"], allow_headers=["*"],
)

_lock = threading.Lock()
_state: dict = {"state": "idle", "progress": 0.0, "message": "", "error": None}


class CalibrateRequest(BaseModel):
    quick: bool = False
    reps: int = Field(5, ge=1, le=15)
    target_seconds: float = Field(0.4, ge=0.05, le=2.0)
    watts: float | None = Field(None, gt=0, le=500)


class MeasureRequest(BaseModel):
    before: dict
    after: dict | None = None
    reps: int = Field(5, ge=1, le=15)
    target_seconds: float = Field(0.4, ge=0.05, le=2.0)


@app.get("/api/health")
def health():
    domains, problems = energy.find_rapl()
    return {
        "status": "ok",
        "rapl_available": bool(domains),
        "rapl_problems": problems,
        "compiler": energy.find_compiler(),
        "profile_exists": config.PROFILE_PATH.exists(),
        "calibration": _state["state"],
    }


@app.get("/api/profile")
def get_profile():
    p = profile.load_profile()
    if p is None:
        raise HTTPException(404, "No hardware profile yet. Run `python scripts/calibrate.py` or POST /api/calibrate.")
    p = dict(p)
    p.pop("raw", None)                      # keep the response small; the raw data stays in data/profile.json
    return p


def _calibrate_job(req: CalibrateRequest) -> None:
    def progress(frac: float, msg: str) -> None:
        _state.update(progress=frac, message=msg)

    try:
        reps, target = (3, 0.2) if req.quick else (req.reps, req.target_seconds)
        sizes = runner.SIZES
        if req.quick:
            sizes = [s for s in sizes if s in (8 << 10, 32 << 10, 128 << 10, 1 << 20, 8 << 20, 64 << 20, sizes[-1])]
        p = profile.calibrate(reps, target, req.watts, False, sizes, progress)
        profile.save_profile(p)
        _state.update(state="done", progress=1.0, message="Calibration finished", error=None)
    except Exception as e:                  # surface any failure to the UI
        traceback.print_exc()
        _state.update(state="error", error=str(e), message="Calibration failed")


@app.post("/api/calibrate", status_code=202)
def start_calibration(req: CalibrateRequest | None = None):
    req = req or CalibrateRequest()
    with _lock:
        if _state["state"] == "running":
            raise HTTPException(409, "A calibration is already running")
        _state.update(state="running", progress=0.0, message="Starting…", error=None)
    threading.Thread(target=_calibrate_job, args=(req,), daemon=True).start()
    return _state


@app.get("/api/calibrate/status")
def calibration_status():
    return _state


@app.post("/api/measure")
def measure(req: MeasureRequest):
    if _state["state"] == "running":
        raise HTTPException(409, "Calibration is running; measurements would disturb it. Try again when it finishes.")
    with _lock:                               # one measurement at a time: parallel runs would corrupt each other
        try:
            return service.measure(req.before, req.after, req.reps, req.target_seconds, profile.load_profile())
        except service.MeasureError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)


if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")

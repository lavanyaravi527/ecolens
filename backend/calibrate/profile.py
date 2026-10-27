"""Run a full calibration and produce / persist the hardware profile."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from typing import Callable

from .. import config
from . import energy, fit, runner

SCHEMA = 1


def load_profile() -> dict | None:
    try:
        return json.loads(config.PROFILE_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_profile(p: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.PROFILE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=2))
    os.replace(tmp, config.PROFILE_PATH)


def describe_environment() -> dict:
    cc = energy.find_compiler()
    ccv = ""
    if cc:
        try:
            ccv = subprocess.check_output([cc, "--version"], text=True).splitlines()[0]
        except Exception:
            pass
    return {
        "cpu": energy.cpu_name(),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "logical_cores": os.cpu_count(),
        "compiler": ccv,
    }


def calibrate(reps: int = 5, target: float = 0.4, watts: float | None = None,
              require_rapl: bool = False, sizes: list[int] | None = None,
              progress: Callable[[float, str], None] | None = None) -> dict:
    domains, problems = energy.find_rapl()
    if domains:
        source, assumed = "rapl", None
        power = None
    else:
        if require_rapl:
            raise runner.CalibrationError("RAPL required but unavailable: " + "; ".join(problems))
        source = "time_proxy"
        assumed = watts if watts is not None else config.DEFAULT_ASSUMED_WATTS
        power = assumed

    idle = energy.measure_idle_watts(domains) if domains else None
    raw = runner.collect(reps, target, domains, power, sizes, progress)
    f = fit.fit(raw)
    if source == "time_proxy":
        f["quality"]["warnings"].insert(0,
            "No hardware energy counter was available. Energy = measured time x "
            f"{assumed:g} W ({'given by you' if watts is not None else 'DEFAULT, not measured'}). "
            "The ratios between operations are measured; the absolute joule scale is not.")
    else:
        f["quality"]["warnings"] = [*problems, *f["quality"]["warnings"]]

    curve = f["memory_curve"]
    weights = dict(f["weights_pj"])
    weights["dram"] = curve[-1]["rand_pj"]            # random-access penalty at the largest working set
    weights["fill"] = curve[-1]["seq_pj"]             # streaming penalty per element at the largest working set
    f["provenance"]["dram"] = "random 4-byte loads over the largest buffer minus the same loop over 8 KB"
    f["provenance"]["fill"] = "sequential loads over the largest buffer minus the same loop over 8 KB"

    return {
        "schema": SCHEMA,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": describe_environment(),
        "energy_source": source,
        "rapl_domains": [d.name for d in domains],
        "power_assumed_w": assumed,
        "idle_watts": idle,
        "l1_bytes": energy.l1d_bytes(),
        "settings": {"reps": reps, "target_seconds": target,
                     "largest_buffer_bytes": curve[-1]["bytes"]},
        "weights_pj": weights,
        "weights_sd_pj": {**f["weights_sd_pj"], "dram": curve[-1]["rand_sd"], "fill": curve[-1]["seq_sd"]},
        "provenance": f["provenance"],
        "memory_curve": curve,
        "quality": f["quality"],
        "raw": raw,
    }

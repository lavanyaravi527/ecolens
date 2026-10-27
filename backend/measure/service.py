"""Compile the user's program (before / after fixes) and measure it for real."""
from __future__ import annotations

import json
import os
import statistics as st
import subprocess
import tempfile
from pathlib import Path

from .. import config
from ..calibrate import energy
from .codegen import CodegenError, program_to_c

try:                                   # Linux/macOS: cap memory and CPU time of the child
    import resource

    def _limits() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
        resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
except ImportError:                    # Windows
    _limits = None  # type: ignore


class MeasureError(RuntimeError):
    pass


def _compile(cc: str, c_src: str, workdir: Path, name: str) -> Path:
    src, exe = workdir / f"{name}.c", workdir / name
    src.write_text(c_src)
    r = subprocess.run([cc, "-O0", "-w", "-I", str(config.NATIVE_DIR), str(src), "-o", str(exe)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise MeasureError("internal compile error:\n" + r.stderr[:2000])
    return exe


def _run(exe: Path, target: float, domains, watts: float | None) -> dict:
    cmd = [str(exe), str(target)] + energy.rapl_args(domains)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60 + 10 * target,
                           preexec_fn=_limits if _limits and os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise MeasureError("program ran too long (infinite loop or enormous trip count?)")
    if r.returncode:
        hint = {-11: " (segfault: array index out of bounds?)", -8: " (division by zero?)",
                7: " (array size too large)"}.get(r.returncode, "")
        raise MeasureError(f"program crashed with exit code {r.returncode}{hint}. {r.stderr.strip()}")
    d = json.loads(r.stdout)
    joules = d["joules"] if d["joules"] is not None else d["seconds"] * (watts or 0)
    return {"seconds_per_run": d["seconds"] / d["runs"],
            "energy_pj": joules / d["runs"] * 1e12,
            "runs": d["runs"], "result": d["result"]}


def _agg(rows: list[dict]) -> dict:
    e = [r["energy_pj"] for r in rows]
    t = [r["seconds_per_run"] for r in rows]
    m = st.median(e)
    return {"energy_pj": m, "energy_pj_min": min(e), "energy_pj_max": max(e),
            "cv": (st.pstdev(e) / st.mean(e)) if len(e) > 1 and st.mean(e) > 0 else 0.0,
            "seconds_per_run": st.median(t), "result": rows[0]["result"],
            "result_stable": len({r["result"] for r in rows}) == 1}


def measure(before: dict, after: dict | None = None, reps: int = 5, target: float = 0.4,
            profile: dict | None = None) -> dict:
    cc = energy.find_compiler()
    if not cc:
        raise MeasureError("No C compiler found on the server (install gcc or clang).")
    try:
        c_before = program_to_c(before)
        c_after = program_to_c(after) if after is not None else None
    except (CodegenError, KeyError, TypeError) as e:
        raise MeasureError(f"Cannot translate program: {e}")

    domains, _ = energy.find_rapl()
    if domains:
        source, watts = "rapl", None
    else:
        source = "time_proxy"
        watts = (profile or {}).get("power_assumed_w") or config.DEFAULT_ASSUMED_WATTS

    with tempfile.TemporaryDirectory(prefix="ecolens-") as td:
        wd = Path(td)
        exes = {"before": _compile(cc, c_before, wd, "before")}
        if c_after is not None:
            exes["after"] = _compile(cc, c_after, wd, "after")
        rows: dict[str, list[dict]] = {k: [] for k in exes}
        for _ in range(max(1, reps)):               # interleave: drift hits both versions equally
            for k, exe in exes.items():
                rows[k].append(_run(exe, target, domains, watts))

    out = {k: _agg(v) for k, v in rows.items()}
    res = {"energy_source": source, "power_assumed_w": watts, "reps": reps, **out,
           "compiler": cc, "flags": "-O0"}
    if "after" in out:
        b, a = out["before"]["energy_pj"], out["after"]["energy_pj"]
        res["saving_pj"] = b - a
        res["saving_percent"] = 100 * (b - a) / b if b > 0 else 0.0
        res["same_result"] = out["before"]["result"] == out["after"]["result"]
    return res

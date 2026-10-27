"""Compile and run the native micro-benchmarks, collect raw numbers."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

from .. import config
from .energy import RaplDomain, find_compiler, rapl_args

COMPUTE = ["empty", "xor", "add", "and", "shl", "shr", "mul", "div", "rem", "mov"]
BASE_BYTES = 8 * 1024
SIZES = [8 << 10, 16 << 10, 32 << 10, 64 << 10, 128 << 10, 256 << 10, 512 << 10,
         1 << 20, 2 << 20, 4 << 20, 8 << 20, 16 << 20, 32 << 20, 64 << 20, 128 << 20, 256 << 20]


class CalibrationError(RuntimeError):
    pass


def build_kernels() -> Path:
    cc = find_compiler()
    if not cc:
        raise CalibrationError("No C compiler found. Install gcc or clang (Windows: MinGW-w64 or WSL; "
                               "macOS: `xcode-select --install`).")
    src = config.NATIVE_DIR / "kernels.c"
    h = hashlib.sha256(src.read_bytes() + (config.NATIVE_DIR / "ecolens_rt.h").read_bytes() + cc.encode()).hexdigest()[:12]
    exe = config.BUILD_DIR / f"kernels-{h}"
    if not exe.exists():
        config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([cc, "-O2", "-I", str(config.NATIVE_DIR), str(src), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode:
            raise CalibrationError("Compiling kernels.c failed:\n" + r.stderr)
    return exe


def run_kernel(exe: Path, name: str, target: float, nbytes: int, domains: list[RaplDomain],
               watts: float | None) -> dict:
    cmd = [str(exe), name, str(target), str(nbytes)] + rapl_args(domains)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120 + 20 * target)
    if r.returncode:
        raise CalibrationError(f"kernel {name} failed: {r.stderr.strip()}")
    d = json.loads(r.stdout)
    if d["joules"] is None:
        if watts is None:
            raise CalibrationError("no energy counter and no --watts given")
        d["joules"] = d["seconds"] * watts
    d["pj_per_iter"] = d["joules"] / d["n"] * 1e12
    return d


def collect(reps: int, target: float, domains: list[RaplDomain], watts: float | None,
            sizes: list[int] | None = None, progress: Callable[[float, str], None] | None = None) -> dict:
    """Returns {"compute": {name: [pj_per_iter per rep]}, "mem": {kernel: {bytes: [...]}}, "arch_has_mov": bool}"""
    exe = build_kernels()
    sizes = sizes or SIZES
    compute_names = list(COMPUTE)
    # `mov` exists only on x86-64 / aarch64 builds
    probe = subprocess.run([str(exe), "mov", "0.01", "0"], capture_output=True, text=True)
    if probe.returncode:
        compute_names.remove("mov")
    mem_jobs = [("seq_reg", BASE_BYTES)] + [(k, s) for s in sizes for k in ("seq", "rand")]
    total = reps * (len(compute_names) + len(mem_jobs))
    done = 0
    raw: dict = {"compute": {k: [] for k in compute_names}, "mem": {}, "n": {}}
    for rep in range(reps):
        for name in compute_names:
            d = run_kernel(exe, name, target, 0, domains, watts)
            raw["compute"][name].append(d["pj_per_iter"])
            raw["n"][name] = d["per_iter"]
            done += 1
            if progress:
                progress(done / total, f"rep {rep + 1}/{reps}: {name}")
        for kname, size in mem_jobs:
            d = run_kernel(exe, kname, target, size, domains, watts)
            raw["mem"].setdefault(kname, {}).setdefault(str(size), []).append(d["pj_per_iter"])
            done += 1
            if progress:
                progress(done / total, f"rep {rep + 1}/{reps}: {kname} {size >> 10} KB")
    return raw

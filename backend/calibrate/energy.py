"""Energy sources: Linux RAPL counters, or wall-clock time x power as a fallback."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

POWERCAP = Path("/sys/class/powercap")


@dataclass(frozen=True)
class RaplDomain:
    name: str
    path: str       # .../energy_uj
    max_uj: int


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def find_rapl() -> tuple[list[RaplDomain], list[str]]:
    """Return (readable package+dram domains, human-readable problems)."""
    problems: list[str] = []
    if not POWERCAP.exists():
        return [], ["no /sys/class/powercap (RAPL is Linux-only; WSL/VMs usually hide it)"]
    domains: list[RaplDomain] = []
    for top in sorted(POWERCAP.glob("intel-rapl:*")):
        if top.name.count(":") != 1:
            continue
        name = _read(top / "name") or ""
        if not name.startswith("package"):
            continue
        cands = [top] + [d for d in sorted(top.glob(f"{top.name}:*")) if (_read(d / "name") or "") == "dram"]
        for d in cands:
            e = d / "energy_uj"
            maxr = _read(d / "max_energy_range_uj")
            if _read(e) is None:
                problems.append(f"cannot read {e} (since Linux 5.10 this needs root: "
                                f"run with sudo or `sudo chmod -R a+r /sys/class/powercap`)")
                continue
            domains.append(RaplDomain(_read(d / "name") or d.name, str(e), int(maxr or 2**40)))
    if not domains and not problems:
        problems.append("powercap exists but exposes no package domain")
    return domains, problems


def rapl_args(domains: list[RaplDomain]) -> list[str]:
    out: list[str] = []
    for d in domains:
        out += ["--rapl", f"{d.path}:{d.max_uj}"]
    return out


def read_total_uj(domains: list[RaplDomain]) -> int:
    return sum(int(Path(d.path).read_text()) for d in domains)


def measure_idle_watts(domains: list[RaplDomain], seconds: float = 2.0) -> float | None:
    if not domains:
        return None
    try:
        t0, e0 = time.monotonic(), read_total_uj(domains)
        time.sleep(seconds)
        t1, e1 = time.monotonic(), read_total_uj(domains)
        if e1 < e0:       # wrapped, give up rather than guess
            return None
        return (e1 - e0) * 1e-6 / (t1 - t0)
    except OSError:
        return None


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    if platform.system() == "Darwin":
        try:
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except Exception:
            pass
    return platform.processor() or platform.machine()


def l1d_bytes() -> int | None:
    try:
        for idx in sorted(Path("/sys/devices/system/cpu/cpu0/cache").glob("index*")):
            if (_read(idx / "level") == "1") and (_read(idx / "type") in ("Data", "Unified")):
                m = re.match(r"(\d+)([KM]?)", _read(idx / "size") or "")
                if m:
                    return int(m.group(1)) * {"": 1, "K": 1024, "M": 1024**2}[m.group(2)]
    except OSError:
        pass
    if platform.system() == "Darwin":
        try:
            return int(subprocess.check_output(["sysctl", "-n", "hw.l1dcachesize"], text=True))
        except Exception:
            pass
    return None


def find_compiler() -> str | None:
    for cc in (os.environ.get("CC"), "gcc", "clang", "cc"):
        if cc and shutil.which(cc):
            return shutil.which(cc)
    return None

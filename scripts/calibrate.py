#!/usr/bin/env python3
"""Calibrate EcoLens on THIS machine and write data/profile.json.

  python scripts/calibrate.py                 # full run (~2-4 minutes)
  python scripts/calibrate.py --quick         # ~30 s, noisier
  python scripts/calibrate.py --watts 18      # no RAPL: energy = time x 18 W
  sudo python scripts/calibrate.py            # Linux: lets us read RAPL counters
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config                      # noqa: E402
from backend.calibrate import profile, runner   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=5, help="repetitions of every kernel (default 5)")
    ap.add_argument("--target", type=float, default=0.4, help="seconds per kernel run (default 0.4)")
    ap.add_argument("--quick", action="store_true", help="3 reps, 0.2 s, fewer buffer sizes")
    ap.add_argument("--watts", type=float, help="active CPU power in watts, used ONLY when RAPL is unavailable")
    ap.add_argument("--require-rapl", action="store_true", help="fail instead of falling back to time x watts")
    ap.add_argument("--max-buffer-mb", type=int, default=256, help="largest buffer for the memory curve")
    a = ap.parse_args()

    reps, target = (3, 0.2) if a.quick else (a.reps, a.target)
    sizes = [s for s in runner.SIZES if s <= a.max_buffer_mb << 20]
    if a.quick:
        sizes = [s for s in sizes if s in (8 << 10, 32 << 10, 128 << 10, 1 << 20, 8 << 20, 64 << 20) or s == sizes[-1]]

    def show(frac: float, msg: str) -> None:
        print(f"\r[{frac:6.1%}] {msg:<40}", end="", flush=True)

    try:
        p = profile.calibrate(reps, target, a.watts, a.require_rapl, sizes, show)
    except runner.CalibrationError as e:
        print(f"\nCalibration failed: {e}", file=sys.stderr)
        return 1
    profile.save_profile(p)
    print(f"\n\nSaved {config.PROFILE_PATH}\n")
    print(f"CPU            : {p['host']['cpu']}")
    print(f"Energy source  : {p['energy_source']}" + (f" ({p['power_assumed_w']} W assumed)" if p['power_assumed_w'] else ""))
    print("Weights (pJ)   :")
    for k, v in p["weights_pj"].items():
        print(f"  {k:6s} {v:12.2f}  ± {p['weights_sd_pj'].get(k, 0):.2f}")
    for w in p["quality"]["warnings"]:
        print("WARNING:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

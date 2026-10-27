"""Turn raw per-iteration energies into the weights the frontend uses.

Everything is a paired difference inside one repetition (so slow drift in
clock speed / temperature cancels), then the median over repetitions.
"""
from __future__ import annotations

import statistics as st

OPS_PER_ITER = 32


def _med(xs):
    return float(st.median(xs))


def _spread(xs):
    return float(st.pstdev(xs)) if len(xs) > 1 else 0.0


def _paired(a: list[float], b: list[float], scale: float = 1.0):
    diffs = [(x - y) / scale for x, y in zip(a, b)]
    return _med(diffs), _spread(diffs), diffs


def fit(raw: dict) -> dict:
    c, mem = raw["compute"], raw["mem"]
    warnings: list[str] = []
    w: dict[str, float] = {}
    sd: dict[str, float] = {}
    prov: dict[str, str] = {}

    def op(name: str, key: str, how: str):
        v, s, _ = _paired(c[name], c["xor"], OPS_PER_ITER)
        if v <= 0:
            warnings.append(f"{key}: measured {v:.3g} pJ <= 0 (noise); clamped to 0")
            v = 0.0
        w[key], sd[key], prov[key] = v, s, how

    w["loop"] = _med(c["empty"])
    sd["loop"] = _spread(c["empty"])
    prov["loop"] = "empty loop, energy per iteration"
    op("add", "alu", "add chain minus xor chain, per op")
    op("and", "and", "and chain minus xor chain, per op")
    op("mul", "mul", "64-bit multiply chain minus xor chain, per op")
    op("div", "div", "64-bit divide chain minus xor chain, per op")
    op("rem", "rem", "64-bit remainder chain minus xor chain, per op")
    # shift = mean of left and right
    vl, sl, _ = _paired(c["shl"], c["xor"], OPS_PER_ITER)
    vr, sr, _ = _paired(c["shr"], c["xor"], OPS_PER_ITER)
    w["shift"], sd["shift"] = max((vl + vr) / 2, 0.0), (sl + sr) / 2
    prov["shift"] = "mean of shl and shr chains minus xor chain, per op"
    if "mov" in c:
        op("mov", "mv", "register move chain minus xor chain, per op")
    else:
        w["mv"], sd["mv"], prov["mv"] = 0.0, 0.0, "not measurable on this CPU architecture; 0 (register renaming)"
        warnings.append("mv: no register-move kernel for this architecture, using 0")

    v, s, _ = _paired(mem["seq"][str(8192)], mem["seq_reg"][str(8192)])
    if v <= 0:
        warnings.append(f"l1: measured {v:.3g} pJ <= 0 (noise); clamped to 0")
    w["l1"], sd["l1"] = max(v, 0.0), s
    prov["l1"] = "sequential load from an 8 KB buffer minus the same loop without the load"

    # memory-penalty curve (per 4-byte access, over and above an L1 hit)
    sizes = sorted({int(k) for k in mem["rand"]})
    base = str(sizes[0])
    curve, run_r, run_s = [], 0.0, 0.0
    for b in sizes:
        r, rs, _ = _paired(mem["rand"][str(b)], mem["rand"][base])
        q, qs, _ = _paired(mem["seq"][str(b)], mem["seq"][base])
        run_r, run_s = max(run_r, r, 0.0), max(run_s, q, 0.0)    # a bigger working set never gets cheaper
        curve.append({"bytes": b, "rand_pj": run_r, "seq_pj": run_s,
                      "rand_sd": rs, "seq_sd": qs})

    # quality: coefficient of variation of every raw series
    worst, worst_name = 0.0, ""
    series = {**{k: v for k, v in c.items()},
              **{f"{k}@{b}": v for k, d in mem.items() for b, v in d.items()}}
    for name, xs in series.items():
        if len(xs) > 1 and st.mean(xs) > 0:
            cv = st.pstdev(xs) / st.mean(xs)
            if cv > worst:
                worst, worst_name = cv, name
    if worst > 0.10:
        warnings.append(f"noisy run: {worst_name} varied {worst:.0%} between repetitions "
                        f"(close other apps, plug in AC power, or raise --reps)")
    return {
        "weights_pj": w,
        "weights_sd_pj": sd,
        "provenance": prov,
        "memory_curve": curve,
        "quality": {"max_cv": worst, "worst_series": worst_name, "warnings": warnings},
    }

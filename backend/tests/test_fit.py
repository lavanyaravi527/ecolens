from backend.calibrate import fit


def synthetic():
    reps = 3
    c = {"empty": [100.0] * reps, "xor": [1000.0] * reps}
    # each op kernel = xor kernel + 32 * (known per-op energy)
    for k, per_op in dict(add=50, and_=55, shl=40, shr=44, mul=190, div=900, rem=910, mov=2).items():
        c[k.rstrip("_")] = [1000.0 + 32 * per_op] * reps
    mem = {"seq_reg": {"8192": [400.0] * reps},
           "seq": {"8192": [430.0] * reps, "65536": [450.0] * reps, "1048576": [520.0] * reps},
           "rand": {"8192": [500.0] * reps, "65536": [700.0] * reps, "1048576": [650.0] * reps}}
    return {"compute": c, "mem": mem}


def test_weights_are_recovered_exactly():
    r = fit.fit(synthetic())
    w = r["weights_pj"]
    assert w["alu"] == 50 and w["and"] == 55 and w["shift"] == 42
    assert w["mul"] == 190 and w["div"] == 900 and w["rem"] == 910
    assert w["loop"] == 100 and w["l1"] == 30


def test_memory_curve_is_monotone_even_if_measurements_are_not():
    r = fit.fit(synthetic())
    rand = [p["rand_pj"] for p in r["memory_curve"]]
    assert rand == sorted(rand)
    assert rand == [0.0, 200.0, 200.0]      # 650 < 700 was clamped up


def test_negative_differences_are_clamped_and_reported():
    raw = synthetic()
    raw["compute"]["add"] = [900.0] * 3     # cheaper than the xor baseline = noise
    r = fit.fit(raw)
    assert r["weights_pj"]["alu"] == 0.0
    assert any("alu" in w for w in r["quality"]["warnings"])


def test_noisy_series_triggers_a_warning():
    raw = synthetic()
    raw["compute"]["mul"] = [3000.0, 9000.0, 3000.0]
    r = fit.fit(raw)
    assert r["quality"]["max_cv"] > 0.1
    assert any("noisy" in w for w in r["quality"]["warnings"])

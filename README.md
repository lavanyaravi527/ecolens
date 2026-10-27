# EcoLens – Green Code Analyzer (with measured backend)

The frontend no longer contains any guessed energy numbers. A small Python backend

1. **calibrates** your machine – runs C micro-benchmarks and records what an add, multiply, divide, shift,
   cache hit and cache miss actually cost (in picojoules), and
2. **measures** your program – translates the mini-language to C, runs the original and the optimised version
   and reports real energy for both.

The browser loads the measured profile at start-up. If there is no profile it refuses to analyse anything
instead of falling back to defaults.

```
ecolens/
├── backend/
│   ├── app.py                  FastAPI: /api/profile, /api/calibrate, /api/measure, serves frontend/
│   ├── calibrate/              native/kernels.c (micro-benchmarks), energy.py (RAPL), runner.py, fit.py, profile.py
│   ├── measure/                codegen.py (validated AST -> C), service.py (compile, run, read energy)
│   └── tests/                  pytest suite (27 tests)
├── frontend/index.html         your page, patched to use the backend (see "What changed")
├── scripts/calibrate.py        command-line calibration
├── data/                       profile.json is written here (git-ignored: every machine calibrates itself)
└── requirements.txt
```

## Quick start

```bash
pip install -r requirements.txt
sudo python scripts/calibrate.py          # Linux: sudo lets it read the CPU's RAPL energy counters
python -m uvicorn backend.app:app --port 8000
# open http://localhost:8000
```

Requirements: Python 3.10+, and `gcc` or `clang` (Linux: `apt install build-essential`; macOS:
`xcode-select --install`; Windows: MinGW-w64 or WSL). You can also skip the command line and press
**Calibrate** in the browser the first time.

`python scripts/calibrate.py --quick` takes about 30 s and is noisier; the default takes a few minutes.
Close other programs and plug in AC power first – the report shows a warning if repetitions disagree by more than 10 %.

### Where the joules come from

| Machine | Energy source | Notes |
|---|---|---|
| Linux, Intel/AMD, run as root | **RAPL package + DRAM counters** | Real energy. Since Linux 5.10 the files need root (`sudo`) or `sudo chmod -R a+r /sys/class/powercap`. |
| Windows, macOS, VMs, WSL, Apple Silicon | **time × watts** | Energy = measured run time × the CPU power you pass (`--watts 15`, or the box in the UI). The *ratios* between operations are measured; the absolute µJ scale is only as good as that wattage. The UI labels this clearly. |

## What the numbers mean

* `weights_pj` in `data/profile.json` are picojoules per operation, from paired differences between kernels
  that share a loop (so loop overhead cancels), median over repetitions, with the spread shown in the UI.
* Memory is a **measured curve**, not "L1 or DRAM": the extra cost of a 4-byte access for 8 KB … 256 MB buffers,
  sequential and random. Arrays in your code are priced by interpolating that curve.
* Whole-package energy over the time the operation takes is attributed to the operation, so idle power is
  included. `idle_watts` is stored in the profile if RAPL is available.
* **Measure on hardware** compiles your program with `-O0` and runs it for ~0.4 s × 5 runs per version,
  alternating before/after so drift hits both equally. `-O0` is deliberate: an optimising compiler would already
  turn `x / 4` into a shift and hoist invariants, and nothing would be left to measure. `*`, `/`, `%` by a
  literal are routed through an opaque macro for the same reason.

## What changed in the frontend (`frontend/index.html`)

* `const M = {alu: 70, …}` → empty; filled from `GET /api/profile`. `DEFAULT_MODEL`, "Reset defaults" and the editable weight boxes are gone
  (the weight panel is now a read-only measured table with spread and provenance).
* `L1 / DRAM / 16` → `memPen(bytes, sequential)` interpolating the measured curve; `2 × ALU` per loop iteration → measured `loop`;
  `%`→`&` fix priced with measured `rem − and`.
* New **Measure on hardware** card: sends the before/after AST to `POST /api/measure` and shows measured vs model.
* First-run **Calibrate** card; "backend unreachable" card with instructions.
* Example `@budget(…)` values are re-based on your machine's totals (the old 60/450 µJ were tuned for the guessed weights).
* Fixed two stray literal `\n` sequences in the original (a visible `\n` between the toolbar buttons and a CSS rule that never applied to `.eco-callout`).
* Self-test "Operation weights affect estimates" now calls `syncOperationCosts()` after changing a weight; two new tests check the profile.

If the page is hosted elsewhere than the backend, set `window.ECOLENS_API = "http://localhost:8000"` before the main script and start the backend with `ECOLENS_CORS=https://your.page.origin`.

## API

| Endpoint | |
|---|---|
| `GET /api/health` | RAPL available? compiler found? profile present? |
| `GET /api/profile` | measured profile (404 until calibrated) |
| `POST /api/calibrate` `{quick, reps, target_seconds, watts}` → 202 | runs in the background |
| `GET /api/calibrate/status` | `{state, progress, message, error}` |
| `POST /api/measure` `{before, after?, reps, target_seconds}` | `before`/`after` = `{body: [...]}` AST from the frontend parser |

## Known limits (please keep these in your report)

* The per-line breakdown is still **static analysis**: loops with non-constant bounds are assumed to run 1000 times,
  and an access is "sequential" only if the index is the loop variable ± a constant (a fixed column stride is priced as
  random, an upper bound – real prefetchers do better). The whole-program number can be checked with Measure on hardware;
  per-line numbers cannot (no per-line energy counters exist).
* "Same value" after a rewrite compares only the returned integer – it is a smoke test, not a proof. `/` and `%` follow C
  (truncating) semantics; `x / 4 → x >> 2` differs for negative `x`.
* Calibration uses 64-bit integer kernels; floating point and SIMD are not modelled (the mini-language has none).
* Run the server on localhost only. `/api/measure` compiles and executes code generated from the posted AST
  (strictly validated: whitelisted operators, identifiers `[A-Za-z_]\w*`, integer literals, size limits, RLIMIT_AS/CPU on Linux/macOS),
  but it is not a sandbox for a public deployment.

## Tests

```bash
python -m pytest            # 26 fast tests (the measurement ones need gcc/clang)
RUN_SLOW=1 python -m pytest # also runs a full quick calibration through the API (~40 s)
```

## Pushing to GitHub

```bash
cd ecolens
git init -b main
git add .
git commit -m "EcoLens: measured cost model backend + patched frontend"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

To merge into an existing frontend repo instead: copy `backend/`, `scripts/`, `requirements.txt`, `pytest.ini`,
`.gitignore`, `.github/` into it and replace your HTML with `frontend/index.html`.

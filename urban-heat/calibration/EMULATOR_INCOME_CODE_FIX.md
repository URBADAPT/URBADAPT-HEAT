# Code fix for Giacomo: make the emulator income `csv` path repo-relative (portable)

**Why:** the 40-city configs now wire emulator income as `income.emulator.csv: "emulated/<city>_p_inc.csv"`
(repo-relative, so a fresh clone resolves it under `data/<city>/emulated/`). But NB05 currently reads
that path **raw**, so only an **absolute** path works today (the Rome pilot hard-codes `/Users/...`).
This must be fixed before the emulator cities can run on a machine without that absolute path.

## The bug (verified 2026-07-07)
- `05_AC_*.ipynb` cell 23: `inc_agg = load_emulator_inc_agg(INCOME_SPEC, zone_match, ...)`.
- `cityheat/income_source.py::load_emulator_inc_agg` does `pd.read_csv(spec["csv"])` — **raw**, no base-dir.
- `resolve_income_inputs` returns `spec["csv"] = emu["csv"]` unchanged.
- So a relative `emulated/<city>_p_inc.csv` is read relative to the **notebook CWD**, not `data/<city>/`.
- (The observed path is fine: cell 22 already does `Path(x) if is_absolute else P(x)`. Only the emulator
  branch skips that.)

## The fix (one line, NB05 cell 23 — mirrors the observed cell-22 logic)
Resolve the spec's csv with the same absolute-or-`P()` rule **before** calling the loader:

```python
if INCOME_SOURCE == 'emulator':
    from cityheat.income_source import load_emulator_inc_agg
    from pathlib import Path
    _csv = INCOME_SPEC['csv']
    INCOME_SPEC = {**INCOME_SPEC,
                   'csv': str(Path(_csv) if Path(str(_csv)).is_absolute() else P(_csv))}   # <-- add
    _min_cov = float((cfg.get('income', {}).get('emulator', {}) or {}).get('min_coverage', 0.95))
    inc_agg = load_emulator_inc_agg(INCOME_SPEC, zone_match,
                                    zone_codes=zone_ref['zone_code'], min_coverage=_min_cov)
```

Alternatively add an optional `base_dir` arg to `load_emulator_inc_agg` and resolve there — but keeping it
in NB05 (which owns `P()`) is smaller and matches the observed branch. Then fix the Rome pilot config to
`income.emulator.csv: "emulated/rome_p_inc.csv"` (drop the absolute `/Users/...`).

**Scope:** shared model code (`05_AC` template + `March2026_agnostic/*/05_AC`). Behaviour-preserving for
absolute paths; only makes relative paths resolve. Needs your sign-off since it's the AC pipeline.

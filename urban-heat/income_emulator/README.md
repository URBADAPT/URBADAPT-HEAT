# income_emulator

Estimate the **within-city income distribution** across sub-city zones for European cities
that lack observed income, trained on the cities in URBADAPT-HEAT that have it.

Output per zone:
- `income_index_pred` — relative income index, **1.0 = municipal population-weighted mean**.
- `p_inc` — population-weighted income percentile rank (0–1), drop-in for the `05_AC`
  income-ranked AC sigmoid.

See `METHODOLOGY.md` for the rationale (scale-free relative target, within-city feature
transform, leave-one-city-out validation).

## Install

```bash
pip install -r requirements.txt
```

The model + cross-validation run with **numpy + pandas + pyyaml** alone (a built-in ridge
fallback). Install `lightgbm` (preferred) or `scikit-learn` for gradient-boosted trees;
install `geopandas`/`rasterio`/`rasterstats` only if you build the predictor table from
rasters with `zonal.py`.

## Quick start (synthetic smoke test)

```bash
python make_synthetic.py --out data/subdivisions.csv
python run.py --config config.yaml
```

Writes to `results/`: `income_index_predictions.csv`, `cv_report.csv` (per-held-out-city
Spearman + MAE), `run_summary.json`.

## Use with your data

Provide one CSV/GeoPackage row per sub-city zone. Required columns (names configurable in
`config.yaml`): zone id, city id, population, income (blank/NaN for cities to predict), plus
the predictor columns. A city is used for training only if it has ≥2 labelled zones. Then:

```bash
python run.py --config config.yaml
```

## Layout

```
config.yaml          # paths, column names, predictors, model, validation
run.py               # orchestrator: split -> CV -> refit -> predict
make_synthetic.py    # synthetic data generator for smoke-testing
pipeline/
  schema.py          # input validation + train/predict split
  features.py        # relative-index target, within-city transforms, p_inc rank
  model.py           # lightgbm / sklearn / numpy-ridge backends
  evaluate.py        # leave-one-city/country-out cross-validation + metrics
  zonal.py           # optional: build the predictor table from GHS/census rasters
```

## Integration with URBADAPT-HEAT

The predictor rasters (GHS-POP/BUILT/AGE, GISCO 2021 `ESTAT_OBS-VALUE-*`) are already
reprojected to the reference grid by `cityheat/vulnerability_layer.py`; aggregate them to
zones to build the predictor table, predict `p_inc` for unlabelled cities, and feed it into
`notebooks/.../05_AC_*.ipynb`.

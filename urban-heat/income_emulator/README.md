# income_emulator

Estimate the **within-city income distribution** across sub-city units for European
cities that lack observed income, trained on the cities in URBADAPT-HEAT that have it.

The target is deliberately **scale-free** — only each unit's position *within* its city —
because absolute income is not comparable across the 11 countries' income definitions.
The default models the **within-city income percentile** directly.

Output per sub-city unit (`results/income_index_predictions.csv`):
- `p_inc` — within-city income percentile (0–1), the drop-in for the `05_AC_*`
  income-ranked AC sigmoid.
- `income_index_pred` — the model's raw target prediction: the within-city percentile
  under the default rank target, or a relative-to-median income index (1.0 = city median)
  if you switch back to the log-index target (see the `target:` block in
  `config_urbadapt.yaml`).

See `METHODOLOGY.md` for the rationale and `HANDOUT.md` for data locations and status.

## Install

```bash
pip install -r requirements.txt
```

The model + cross-validation run with **numpy + pandas + pyyaml** alone (a built-in ridge
fallback) and pick up **lightgbm** for gradient-boosted trees if installed. Building the
covariate table from rasters additionally needs **geopandas, pyogrio, rasterio,
rasterstats, pyarrow** (all in requirements.txt). On Python 3.14 use `pyogrio`, not
`fiona` (no 3.14 wheel) — geopandas 1.x uses pyogrio by default.

## Real run (URBADAPT data)

`config_urbadapt.yaml` drives the real two-stage pipeline. Its inputs — per-country
sub-city boundaries, the harmonized income labels, and the GHSL / LCZ / Eurostat 2021
census / facility layers — are documented in `HANDOUT.md`; the multi-GB rasters live on
OneDrive and are not in the repo.

```bash
# 1. Athens labels: population-weighted extraction of the rasterized Athens income layer
#    onto the synoikia districts -> data/income/income_subcity_harmonized_athens.csv.
#    Required before step 2, because io.income_labels points at its output.
python athens_ingest.py --config config_urbadapt.yaml

# 2. Build the per-unit covariate table from the rasters/vectors (slow: windows the
#    global GHS/LCZ GeoTIFFs per city). -> data/covariates_subcity.csv
python covariates.py    --config config_urbadapt.yaml

# 3. Merge labels, leave-one-country-out cross-validate, refit, predict. -> results/
python run.py           --config config_urbadapt.yaml

# optional: compare targets (percentile rank vs log index) x CV schemes
python cv_compare.py    --config config_urbadapt.yaml
```

Writes to `results/`: `income_index_predictions.csv`, `cv_report.csv` (per-held-out-city
Spearman + MAE), `cv_out_of_fold_predictions.csv`, `run_summary.json`.

Every unit in the covariate table receives a `p_inc`, including unlabelled ones — the
intended use is to predict cities without observed income. Wiring a genuinely new city in
means adding its boundary layer to `boundaries_manifest` *and* a `(country, subcity_code,
city)` row (income left blank) to the labels file, so the boundary→city join in
`covariates.py` can place it.

## Preliminary skill

Across 15 countries / 152 cities, zone-weighted Spearman of predicted vs observed
within-city ordering:
- **leave-one-country-out** (honest transfer to a country with *no* income data): **~0.57**
  — strong for well-sampled countries (AT/FI/DK/NL/SE/BE/IT 0.66–0.85), lower for the
  large, recently-added folds held out whole (NO ~0.53, IE ~0.55).
- **leave-one-city-out** (a new city where the country is already sampled): **~0.68**,
  reaching 0.9–1.0 where many sibling cities exist (e.g. Norway's 109 municipalities).

Great Britain is **excluded by default** (`filters.require_non_null`): the UK is absent
from the Eurostat 2021 census grid, so its units have no demographic covariates (including
it lowers the average further).

## Quick start (synthetic smoke test, no real data)

```bash
python make_synthetic.py --out data/subdivisions.csv
python run.py --config config.yaml
```

## Layout

```
config_urbadapt.yaml # REAL config: paths, columns, rank target, GB filter, CV scheme
config.yaml          # synthetic smoke-test config
athens_ingest.py     # population-weighted raster -> synoikia income labels for Athens
covariates.py        # STAGE 1: build the per-unit covariate table from rasters/vectors
run.py               # STAGE 2: merge labels -> filter -> CV -> refit -> predict
cv_compare.py        # leave-one-country-out vs leave-one-city-out x rank vs index target
make_synthetic.py    # synthetic data generator for smoke-testing
pipeline/
  schema.py          # input validation + train/predict split
  features.py        # within-city target + transforms, p_inc rank, renormalisation
  model.py           # lightgbm / sklearn / numpy-ridge backends
  evaluate.py        # leave-one-country/city-out cross-validation + metrics
  zonal.py           # legacy generic raster->zone helper (superseded by covariates.py)
```

## Integration with URBADAPT-HEAT

Feed the `p_inc` column of `results/income_index_predictions.csv` into the income-ranked
AC sigmoid in `notebooks/.../05_AC_*.ipynb`, so cities without observed income can run the
AC penetration / heat-mortality / equity analyses.

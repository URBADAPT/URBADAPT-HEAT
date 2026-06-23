# Handout: within-city income distribution emulator

Briefing for a Claude Code session picking up this project. Read this first, then
run / edit as needed. Paths are for Giacomo's machine; adjust if the repo moved.

## 1. What this is

An emulator that learns the **within-city income distribution** from cities that
have observed sub-city income, then predicts it for cities that do not, using
pan-European covariates (GHSL built form, LCZ, Eurostat 2021 census grid,
facility access).

Target is **scale-free**: `inc_rel_to_city_median` (= 1.0 at the city median),
modelled as its log. The pipeline also emits **`p_inc`**, the within-city income
percentile rank (0-1), which is what URBADAPT-HEAT's AC-downscaling step
(`notebooks/.../05_AC_*.ipynb`) consumes. Absolute income is deliberately not
modelled (incomparable across the 11 countries' income concepts).

## 2. Where it lives

```
urban-heat/income_emulator/
  config.yaml              # synthetic demo config
  config_urbadapt.yaml     # REAL config (use this) - paths, columns, target, CV
  covariates.py            # STAGE 1: build per-unit predictor table from rasters
  run.py                   # STAGE 2: merge labels, cross-validate, predict
  make_synthetic.py        # synthetic data generator (smoke test only)
  pipeline/
    schema.py              # validation + train/predict split
    features.py            # target build, within-city transforms, p_inc, renorm
    model.py               # lightgbm -> sklearn -> numpy-ridge backends
    evaluate.py            # leave-one-country/city-out CV + metrics
    zonal.py               # generic raster->zone helper (superseded by covariates.py)
  METHODOLOGY.md           # rationale and limitations
  README.md                # usage
  data/                    # GITIGNORED - inputs + outputs live here
```

## 3. Data inputs (all confirmed present)

- Labels: `data/income/income_subcity_harmonized.csv` — 4,755 sub-city units,
  36 cities, 11 countries. Key columns: `country, city, subcity_code, inc_level,
  inc_rel_to_city_median, inc_pct_within_city`.
- Boundaries: `data/boundaries/*.gpkg` — 11 per-country files (BE/CH/DK/ES/FI/FR/
  GB/IT/NL/PT/SE). Listed in `config_urbadapt.yaml: covariate_sources.boundaries_manifest`.
  Join: `boundary_code` -> `subcity_code` (DK joins on district name). Verified
  97-100% per country; 4733/4734 features get a city.
- Census: `data/gisco/Eurostat_Census-GRID_2021_V3/.../ESTAT_Census_2021_V3.parquet`
  (1 km, EPSG:3035, all 13 variables).
- Facilities: `data/gisco/EU_health_facilites.gpkg`, `EU_education_facilites.gpkg`.
- GHSL + LCZ + UCDB: on OneDrive, paths hard-set in `config_urbadapt.yaml`
  (`socioecon/GHS_POP|BUILT_V|BUILT_H ... R2023A`, `climate/lcz/lcz_filter_v3.tif`,
  `boundaries/GHS_STAT_UCDB2015...gpkg`). These are multi-GB and OneDrive-synced.

## 4. How to run

```bash
cd urban-heat/income_emulator
pip install -r requirements.txt          # geopandas, rasterio, rasterstats, pyarrow, lightgbm
python covariates.py --config config_urbadapt.yaml   # -> data/covariates_subcity.csv
python run.py        --config config_urbadapt.yaml   # -> results/
```

`covariates.py` is the slow step: it windows the global GHS/LCZ rasters per city.
**Ensure the OneDrive rasters are downloaded locally (not cloud-only placeholders)**
or rasterio will fail. Outputs in `results/`: `income_index_predictions.csv`
(`income_index_pred`, `p_inc`), `cv_report.csv` (per-held-out-country Spearman +
wmae), `run_summary.json`.

Smoke test without real data: `python make_synthetic.py && python run.py --config config.yaml`.

## 5. Status

Done and verified: label wiring, leave-one-country-out CV, median renormalisation,
`p_inc` emission, boundary assembly (attribute-level join checked). The income side
was run end-to-end on the real 36-city file with placeholder covariates (per-city
median of predicted index = 1.000, p_inc in 0-1, learns injected signal).

NOT yet done: `covariates.py` has never actually executed (this environment lacked
geopandas/rasterio). First real run is the priority; expect to debug raster
CRS/nodata and the census areal-overlay step.

## 6. Likely edit points / known issues

1. **First covariates.py run will need debugging.** Watch: GHS nodata values
   (script uses `nodata=-200` as a placeholder guard — check actual GHS nodata),
   LCZ class codes (grouping in `LCZ_GROUPS` assumes Demuzere 1-17; confirm against
   `climate/lcz/readme.txt`), and the census GRD_ID parser (`_parse_grd_id`).
2. **GHS-BUILT-H mean** is taken over all pixels; consider masking to built pixels
   only for a cleaner height signal.
3. **Rank vs index target (open decision).** Currently models `log(inc_rel_to_city_median)`.
   With 7 income concepts across countries, the pure rank `inc_pct_within_city` may
   transfer better. To test: set `target.precomputed_column: inc_pct_within_city`,
   `target.log_ratio: false`, `postprocess.renormalise_mode: none`, rerun, compare
   `cv_report.csv` Spearman. Worth wiring as a clean switch and running both.
4. **Denmark** drops 1 district (name spelling mismatch between income file and
   `DK_Copenhagen_districts.gpkg`); reconcile if you want all 10.
5. **Athens** is excluded (not in the harmonized file; income is raster-based, see
   `data/boundaries/income_athens_raster.csv` + `GR_Athens_synoikia_districts.gpkg`).
   Add a small ingestion if Athens is wanted.
6. **data/covariates_subcity.csv** currently in the tree is a RANDOM placeholder
   from verification; `covariates.py` overwrites it on first real run.
7. **MAUP**: unit size varies hugely across countries (London MSOA vs Italian CAP).
   `unit_area_km2` is included as a control; watch per-country CV and consider
   stratifying or adding units-per-city.
8. **GRDI deprivation** (`socioecon/povmap-grdi-v1.tif`) was deliberately excluded
   (circularity with income). Re-add only if you also report results without it.

## 7. Git note (important)

The repo has stale zero-byte locks (`.git/HEAD.lock`, `.git/index.lock`,
`.git/objects/maintenance.lock`) left by a sandbox that couldn't unlink them.
**Delete those three files before using git**, or commits will fail with
"Another git process seems to be running". Work is on branch
`feature/income-emulator` (commits `df70aba`, `29d3aa0`, `ebefc7d`); push with
`git push -u origin feature/income-emulator` once locks are cleared.

## 8. Integration target

Feed `results/income_index_predictions.csv` `p_inc` into the existing
`05_AC_*.ipynb` income-ranked AC sigmoid so cities without observed income can run
the AC penetration / heat-mortality / equity analyses.

# Income emulator — validation suite

Reproducible scripts + reference data behind the emulator's validation claims. Run from
`income_emulator/` in the `urbanheat` env (needs `lightgbm`, `geopandas`, `rasterstats`,
`osmnx`, `matplotlib`). All metrics use the canonical pop-weighted Spearman
(`pipeline.evaluate._spearman`), comparable to `results/cv_report*`.

## What's here
| file | what it does |
|---|---|
| `leave_one_city_out.py` | Honest leave-one-city-out skill for labelled cities (train on all others). |
| `build_boundary_osm.py` | Fetch a city's sub-city boundary from OSM as a deploy-ready `.gpkg`. |
| `deploy_new_city.sh` | One-shot: OSM boundary → `deploy_predict` → `map_and_rank` for a new city. |
| `map_and_rank.py` | Choropleth + ranked poorest/richest table for a deploy run (human verification). |
| `validate_munich.py` | Objective Spearman of a Munich deploy vs **published** income per Stadtbezirk. |
| `prove_joinkey.py` | Demonstrates the emulator zone-join bug + the `cityheat.income_source` fix. |
| `reference/munich_income_stadtbezirk_2020.csv` | Munich income by Stadtbezirk (2020), with source citation. |
| `results/` | Berlin + Munich deploy outputs + maps (the committed evidence). |

## Headline results (this code reproduces them)
- **Pipeline reproduces** the committed CV: `python run.py --config config_urbadapt.yaml` → zone-weighted
  Spearman ≈ 0.60 (committed 0.59).
- **Leave-one-city-out** (`leave_one_city_out.py`, canonical pop-weighted Spearman — matches `cv_report`):
  Milan ≈ 0.82, Madrid ≈ 0.70, Rome ≈ 0.69, Amsterdam ≈ 0.65, Barcelona ≈ 0.56.
- **Munich, objective, zero German training data:** Spearman **0.69** (median income, pop-weighted 0.67)
  vs published Stadtbezirk income — right at the leave-one-country-out expectation.
- **Berlin, human verification:** recovers the rich-SW / poor-centre gradient (Grunewald/Wannsee/Nikolassee
  top; Märkisches Viertel/Gesundbrunnen/Gropiusstadt/Neukölln bottom) with zero German data.

## Known, interpretable failure modes (state these as limitations)
- **Dense historic centres are under-ranked** (Rome `00186`, Munich Altstadt-Lehel = observed #1 richest
  but predicted mid). Needs a **construction-era covariate** (GHSL multitemporal built-age), not yet wired.
- **East-German Plattenbau is over-ranked** (Berlin Hellersdorf/Marzahn). The training set has **no eastern
  cities with income**, so no covariate can fix it until such cities are added.
- A city-scale sprawl/compactness *context* feature was A/B-tested and **did not help** (overall +0.009 within
  noise; Italy and tail-hit slightly worse) — a whole-city signal can't fix a zone-level problem.

## End-to-end NB05 emulator run (C3) + income-coverage note
`results/c3_nb05_emulator_rome/` holds a **full executed NB05 run** with `income.source: emulator`
(Rome): the executed notebook + `income_provenance_rome.json`. It completed with **0 errors**, and the
AC calibration ran on the emulator `p_inc` (k\* = 1.39–1.51, identical to the observed run) — i.e. the
income switch drives the real notebook end-to-end, not just a replication.

**Income-coverage caveat:** Rome's NB05 AC layer has **118** CAP zones (`CAPZONE.shp` clipped to the FUA),
but the income dataset / emulator covers **72** of them, so 46/118 zones get the city-mean income in
**both** observed and emulator mode. The emulator's coverage assert surfaces this (it fired at the default
`min_coverage=0.95`; the Rome test sets `income.emulator.min_coverage: 0.6`). For a genuine deployment
city the emulator boundary **is** the AC-zone boundary, so coverage is ~100% and the default applies.

## Reproduce
```bash
cd income_emulator                      # urbanheat env

# 1) honest held-out skill on labelled cities
python validation/leave_one_city_out.py --config config_urbadapt.yaml Roma Madrid Amsterdam Milano

# 2) deploy to an unlabelled city (needs config_local.yaml with local GHSL+LCZ raster paths)
python validation/build_boundary_osm.py --place "Muenchen, Germany" --admin-level 9 \
    --city Munich --out data/DE_Munich_stadtbezirke.gpkg --layer munich_stadtbezirke
python deploy_predict.py --boundary data/DE_Munich_stadtbezirke.gpkg --layer munich_stadtbezirke \
    --key boundary_code --city Munich --country DE --exclude Munich --config config_local.yaml \
    --out validation/results/deploy_munich.csv
python validation/map_and_rank.py --pred validation/results/deploy_munich.csv \
    --boundary data/DE_Munich_stadtbezirke.gpkg --layer munich_stadtbezirke --title Munich \
    --out validation/results/munich_p_inc_map.png

# 3) objective check vs published income
python validation/validate_munich.py --pred validation/results/deploy_munich.csv

# 4) the join-key bug + fix (name-keyed city)
python validation/prove_joinkey.py --city Copenhagen --match-by name \
    --boundary <path>/DK_Copenhagen_districts.gpkg --boundary-key boundary_name
```

## Tests (the integration logic)
`cityheat/tests/test_income_source.py` (the `observed|emulator` switch + join key + coverage assert) and
`cityheat/tests/test_observed_regression.py` (observed mode unchanged) — run with `pytest` or `python`.

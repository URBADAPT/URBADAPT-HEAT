# LST-T2M climate emulator

URBADAPT-HEAT translates land-surface-temperature changes from street greening into near-surface air
temperature with a pooled emulator, read from
`outputs/<city>/emulator/bundles/emulator_bundle_pooled_all_quadratic_holdout_safe.json`
(model `pooled_all_quadratic_holdout_safe`, 130 cities, 2008-2017, `t2m_center` 17.0117 C,
33,391,358 observations, SHA-256 `30750457b4b07c2a...`). The specification is described in the
model description paper (Sect. "Climate emulator for rapid variable translation").

## Files

| Path | Content |
|---|---|
| `scripts/estimate_climate_emulator_LST_T2M_multicities.R` | Downloads PROVIDE/UrbClim daily LST and T2M per city, builds and caches the per-city panels (version of 19 May 2026). |
| `scripts/estimate_climate_emulator_validation.R` | Panel loading, the LCZ-stratified point sampler and validation helpers; sourced by the export script (version of 19 May 2026). |
| `scripts/export_emulator_bundle_pooled_142cities_asrun.R` | Pools the city panels, fits the emulator with `fixest::feols`, writes the bundle, and runs the leave-one-city-out validation, exactly as run (see below). |
| `session/R_history_2026-05-19_20.txt` | Verbatim R history of the session that produced the bundle. |
| `results/` | The bundle and the run's diagnostics and validation tables: `pooled_all_panel_diagnostics.csv` (points and observations per city after sampling), `pooled_all_city_holdout_evaluation.csv` and `pooled_all_city_holdout_summary.csv` (leave-one-city-out). |

## How the fit was run

The fit was run interactively on 19-20 May 2026 on a Windows workstation with 64 GB of RAM, from
cached city panels (about 30 GB). Memory limits led to two sampling steps:

1. at load time, 33 % of the grid points of each city, stratified by Local Climate Zone (at least
   5 points per stratum, seed 42), with all days of each kept point (`POOL_SAMPLE_FRAC = 0.33`);
   this gives the 333,909,147 observations in `pooled_all_panel_diagnostics.csv`;
2. before the fit, 10 % of the observations in each city x LCZ group, which gives the 33,391,358
   observations of the bundle. `t2m_center` is the pooled mean before this second step.

The leave-one-city-out validation reuses the same subsampled panel. The as-run script was
reconstructed from the session history: the first lines of the script, which fell out of R's
512-line history, come from the copy of the script saved on 25 May, and three abandoned fit attempts
that precede the subsample are omitted. One discarded call to `sample()` is
kept because it advanced the random-number state before the subsample that was used.

The cached panels are rebuilt by `estimate_climate_emulator_LST_T2M_multicities.R` from the PROVIDE
UrbClim LST and T2M fields and the covariates listed in the paper. The R packages used are `dplyr`,
`data.table`, `fixest` and `jsonlite` (plus the spatial packages of the panel builder).

Development history of these scripts, including later full-sample variants not used by v1.0.1:
https://github.com/ArmandeCMCC/emulator-142cities

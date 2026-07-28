# natcities_visual_items

R scripts producing the figures and summary tables for the results-focused
Nature Cities manuscript *"Synergies and trade-offs of public and private urban
heat adaptation in European cities"* (`manuscript_NATCITIES.tex`).

## Design: built for 40 cities, not 2

Cities are **auto-discovered** from
`urban-heat/outputs_variants/masselot_main_agnostic/<city>/{tables,figures,root}`;
missing inputs are skipped, never fatal. Everything scales to ~40 cities without
code changes. Override the variant with the `NATCITIES_VARIANT` env var.

Three tiers, so nothing becomes an unreadable 40-bar panel:

1. **Summary figures** (main text) — cross-city, **no per-city panels**. Every
   panel is a distribution or scatter (one point = one city), colored by
   **climate cluster** and using **normalized** metrics (per-100k, per-capita,
   %, shares, slopes).
2. **Exemplar dashboards** — one rich single-city dashboard per climate cluster
   (the cluster **medoid**), holding the city-specific detail kept out of the
   summaries.
3. **Big city tables** — all cities, city-specific rows (CSV + `\input`-able
   LaTeX). This absorbs the per-city detail.

## Climate clustering

`00_city_meta.R` computes, per city, from the 2020 baseline hazard netCDF
(`T2M_daily_mean_2020_FUA_degC*.nc`): warm-season (May–Sep) mean T2M, P95, and
hot-day count (daily-mean ≥ 25 °C). Combined with country/pop/capital from
`configs/<city>.yml`. Cities are k-means clustered (k=3) on
[intensity × frequency]; clusters labelled Hot/Temperate/Cool by centroid heat;
the medoid of each cluster is flagged `is_exemplar`. With <3 cities it falls back
to provisional fixed-threshold labels. Output cached in `tables/city_metadata.csv`
(+ `.city_heat_cache.csv` so the netCDF read isn't repeated).

## ⚠️ Run under PowerShell, not Git Bash

`ncdf4`/`terra` **segfault** under this machine's Git Bash `Rscript` (DLL path
issue). Run everything via PowerShell:

```powershell
cd natcities_visual_items/R
Rscript build_all.R          # metadata -> summaries -> exemplars -> tables
Rscript 00_city_meta.R       # or individual steps
```

Requires R (tested 4.5.3): ggplot2, dplyr, tidyr, readr, jsonlite, stringr,
forcats, scales, patchwork, xtable, ncdf4, yaml.

## Items

| Script | Output | Slot | Content |
|--------|--------|------|---------|
| `00_city_meta.R` | `tables/city_metadata.csv` | — | Climate metrics + clusters + exemplars |
| `fig1_risk_effectiveness.R` | `fig1_risk_effectiveness` | `fig:result1` | Baseline mortality/100k vs heat intensity; %reduction & avoided/100k by pathway |
| `fig2_distribution.R` | `fig2_distribution` | `fig:result2` | Public vs private cost/capita by lever (facet=cluster); equity-vs-efficiency (targeted vs uniform); greening progressivity slope |
| `fig3_synergies.R` | `fig3_synergies` | `fig:result3` | AC mortality ledger (net vs lives added back by waste heat); tree co-benefits recovering cost (electricity + CO₂); % of AC waste-heat penalty cancelled by greening |
| `exemplars.R` | `exemplar_<city>` | figs | One detailed dashboard per cluster medoid |
| `tab_cba_by_city.R` | `tab_cba_by_city` | table | CBA by city × pathway |
| `tab_risk_by_city.R` | `tab_risk_by_city` | table | Hazard/exposure/risk by city |

## Conventions

`_helpers.R` centralises paths, city discovery, safe readers, `load_city_meta()`/
`attach_meta()`, the shared `theme_natcities()`, the pathway palette
(`PATHWAY_COLORS`) and cluster palette (`CLUSTER_COLORS`: Hot=red, Temperate=amber,
Cool=blue), and the `save_item()` / `save_table()` writers.

## Open items

- Clusters + exemplars finalise once ≥3 cities span ≥2 clusters (today Milan &
  Rome are both provisional "Hot"; only Milan has CBA results, so it renders as
  the provisional exemplar).
- `pop_k` missing in some configs (e.g. Rome) → per-100k/per-capita panels drop
  those cities until populated. Could switch the denominator to FUA population
  from the exposure data for precision.
- Fig 2c equity slope is derived in R from municipio SVI + ΔGVI in
  `<city>_policy_comparison.csv`.
- SI sensitivity figures (discount/budget/tree-age, IF comparison, EWS
  warning-days) not yet scripted.

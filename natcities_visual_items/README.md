# natcities_visual_items

R scripts producing the figures and summary tables for the results-focused
Nature Cities manuscript *"Synergies and trade-offs of public and private urban
heat adaptation in European cities"* (`manuscript_NATCITIES.tex`).

## Design: built for 40 cities, not 2

Cities are **auto-discovered** from
`<OUTPUTS_BASE>/<city>/{tables,figures,root,interim}`; missing inputs are
skipped, never fatal. Everything scales to ~40 cities without code changes.

## Inputs: point at the real 40-city runs

`OUTPUTS_BASE` defaults to the in-repo
`urban-heat/outputs_variants/<NATCITIES_VARIANT>/`, which still holds only a
**stale 2-city (Milan/Rome) run**. The authoritative 40-city results live in the
Drive `juno_pull` sync of the cluster runs, so set `NATCITIES_OUTPUTS_BASE`:

```powershell
$env:NATCITIES_OUTPUTS_BASE = "G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\outputs_variants\masselot_main_agnostic"
cd natcities_visual_items/R
Rscript build_all.R
```

Every run prints the resolved path and the number of cities found up front:

```
[inputs] G:\...\juno_pull\outputs_variants\masselot_main_agnostic
[inputs] 40 city(ies) with results  (via NATCITIES_OUTPUTS_BASE)
```

Check that line — **without the env var you will silently render the 2-city
paper.** `NATCITIES_VARIANT` still selects the variant under the in-repo layout.

Google Drive keeps both copies of a sync conflict, as `<city> (1)` or
`<city> 2`. `discover_cities()` drops those (one exists today, `cologne (1)`)
so they cannot enter the figures as an extra city that double-counts a real one.

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

`00_city_meta.R` computes, per city, for the 2020 baseline: warm-season (May–Sep)
mean T2M, P95, and hot-day count (daily-mean ≥ 25 °C). Two interchangeable
sources, in preference order:

1. `interim/hazard_events_T2M_daily_<city>.csv` — daily rows with the city-mask
   spatial mean already reduced (`mean_intensity_citymask_degC`). **This is what
   the cluster runs ship**, and it is the source used for all 40 cities. Filtered
   to 2020 (the file spans 2020–2050).
2. `hazard/T2M_daily_mean_2020_FUA_degC*.nc` — the raw grid, kept as a fallback
   for older local runs. The `juno_pull` tree contains no netCDFs.

The two agree to within 0.03 °C and 1 hot day (verified on Milan and Rome).
`heat_source` in the metadata records which was used; the heat cache is
invalidated automatically if it predates this column.

Cities are k-means clustered (k=3) on [intensity × frequency]; clusters labelled
Hot/Temperate/Cool by centroid heat; the medoid of each cluster is flagged
`is_exemplar`. With <3 cities it falls back to provisional fixed-threshold
labels; cities missing heat metrics are left unclustered rather than aborting the
run. Output cached in `tables/city_metadata.csv` (+ `.city_heat_cache.csv`).

## Pathway selection: read this before adding a figure

`<city>_policy_effectiveness.csv` has several rows per pathway, and the EWS row
carries **whichever attribution wording that city's run produced** — `EWS
(marginal)` / `(counterfactual)` / `(intermediate)`, split 14/14/12 across the
40 cities. A fixed label whitelist (what the scripts used before) therefore
**silently dropped EWS for 26 of 40 cities**.

Always select pathways through `canonical_effectiveness(city)` in `_helpers.R`,
which matches on pathway plus a role pattern rather than an exact label. It
returns one row per pathway — Trees (uniform), AC **net of waste heat**, EWS
(any wording) — and keeps the wording in `policy_variant` so it stays auditable.
The three EWS wordings are pooled safely: each equals that city's `EWS (central)`
row in `<city>_cea_summary.csv` (verified ratio 1.000 for all 40).

For cost-effectiveness prefer `read_cea()`, which reads
`<city>_cea_summary.csv` — the one table whose labels are uniform across all 40
cities (`Trees (base O&M)` / `Trees (5x O&M)` / `AC (GROSS)` / `AC (NET)` /
`AC (NET + trees)` / `EWS (central)`).

## Near-zero-benefit pathways

Madrid and Sevilla greening avoid essentially no deaths (0 and 0.009 over 25
years), so their cost per death is `Inf` or ~1e10 € — an artefact that would
otherwise set the axis for all 40 cities and print as a number in the
manuscript. Anything below `NO_BENEFIT_DEATHS` (0.5 deaths / 25y) is censored:
blank in the tables, omitted from log axes, and counted in the figure subtitle.

Censoring hides the symptom, not the cause: both cities are the extreme end of
the coefficient-coverage problem described under **Open items** below. Read
every greening number against `coef_lst_coverage_pct` in `city_metadata.csv`.

## Population denominator

Per-100k / per-capita normalisation uses **the model's own exposure population**
— the sum of per-region `population` in `<city>_policy_comparison.csv`, which is
the denominator the mortality results are actually computed on. The hand-entered
`pop_k` in `configs/<city>.yml` is only a fallback: it is absent for 4 cities and
off by >2× for others (Porto: 230k in config vs 537k in the model, city-proper vs
wider area). `city_metadata.csv` keeps `pop_k_model`, `pop_k_config` and
`pop_source` so the choice stays auditable.

## ⚠️ Run under PowerShell, not Git Bash

`ncdf4`/`terra` **segfault** under this machine's Git Bash `Rscript` (DLL path
issue). Run everything via PowerShell:

```powershell
cd natcities_visual_items/R
Rscript build_all.R          # metadata -> figures -> SI -> exemplars -> tables
Rscript fig3_costeffectiveness.R   # or individual steps
```

Requires R (tested 4.5.3): ggplot2, dplyr, tidyr, readr, jsonlite, stringr,
forcats, scales, patchwork, ggrepel, xtable, ncdf4, yaml.

## Items

| Script | Output | Slot | Content |
|--------|--------|------|---------|
| `00_city_meta.R` | `tables/city_metadata.csv` | — | Climate metrics + clusters + exemplars |
| `fig1_risk_effectiveness.R` | `fig1_risk_effectiveness` | `fig:result1` | Baseline mortality/100k vs heat intensity (log–log fit); % reduction & avoided/100k by pathway |
| `fig2_distribution.R` | `fig2_distribution` | `fig:result2` | Public vs private cost/capita by lever (facet=cluster); equity–efficiency trade-off of targeting; greening progressivity |
| `fig3_costeffectiveness.R` | `fig3_costeffectiveness` | `fig:result3` | € per death avoided by pathway; normalised budget frontier; deployment order vs budget |
| `fig4_synergies.R` | `fig4_synergies` | `fig:result4` | AC's waste-heat self-penalty vs heat; how little greening offsets it; overlap between the levers |
| `si1_sensitivity.R` | `si1_sensitivity` | SI | Discount rate; greening cost assumptions; greening ambition |
| `si2_if_comparison.R` | `si2_if_comparison` | SI | Four exposure-response functions and what the choice does to baseline mortality |
| `si3_ews.R` | `si3_ews` | SI | Warning days to 2050; share of deaths on warned days; benefit ramp |
| `exemplars.R` | `exemplar_<city>` | figs | One detailed dashboard per cluster medoid |
| `tab_cba_by_city.R` | `tab_cba_by_city` | table | CBA by city × pathway (120 rows) |
| `tab_risk_by_city.R` | `tab_risk_by_city` | table | Hazard/exposure/risk by city |
| `tab_ews_by_city.R` | `tab_ews_by_city` | table | Warning days, avoided deaths, cost per death, attribution wording |
| `tab_synergies_by_city.R` | `tab_synergies_by_city` | table | Waste-heat penalty, penalty removed by trees, lever overlap |
| `make_report.R` | `report.html` | — | One browsable page with every figure and table |

## Browsing the output

`build_all.R` finishes by writing **`report.html`** — every figure and all four
tables on one page, with links to the PDF and LaTeX versions. Open it from
inside `natcities_visual_items/`; images are referenced relatively, so the file
stays ~55 KB and always shows the last build. It is not self-contained, so
copy the `figures/` and `tables/` folders alongside it if you send it on.

## Conventions

`_helpers.R` centralises paths, city discovery, safe readers, the pathway
selectors (`canonical_effectiveness()`, `read_cea()`, `read_cba()`),
`load_city_meta()`/`attach_meta()`, the shared `theme_natcities()`, the pathway
palette (`PATHWAY_COLORS`) and cluster palette (`CLUSTER_COLORS`: Hot=red,
Temperate=amber, Cool=blue), the shared `cluster_scale()` / `eur_log_scale()`
scales, and the `save_item()` / `save_table()` writers.

## Status (real 40-city runs, 2026-08-10)

**40 city folders, all 40 complete** — the five partial cities of the 2026-08-03
snapshot (`cologne`, `dublin`, `ljubljana`, `rome`, `rotterdam`) have been
rerun. Clusters: 17 Cool / 13 Temperate / 10 Hot, medoids **Amsterdam /
Budapest / Palermo**. The whole pipeline runs end to end with no failures and no
warnings.

### What the real numbers say

- Baseline heat mortality rises **×1.58 per °C** of warm-season mean temperature
  (R² = 0.69 in log space), from Dublin (~0.02 / 100k) to Athens (168 / 100k).
- **Warnings are ~100× cheaper per life than either physical lever**: median
  €66k per death avoided, against €6.4M for AC and €17.6M for greening. The
  ranking is unchanged by the discount rate (1–7%).
- **AC undermines itself**, and worst where it is needed most: the waste-heat
  penalty takes back **+3.2 pp of AC's gross benefit per °C**, reaching 69% in
  Athens and 68% in Porto.
- **Income-targeting AC support is cheap, not free**: median **+15 pp** of the
  mortality benefit shifted to the two most vulnerable quintiles for **4.8%** of
  total lives saved — and 13 of 40 cities sit in the win-win quadrant, where
  targeting is both more equitable *and* saves more lives.
- **Uniform greening is already progressive.** Equity targeting steepens the
  ΔGVI-on-vulnerability gradient in only 18 of 40 cities, because the uniform
  rule already concentrates planting in low-canopy districts, which are also
  the vulnerable ones.

### Panels retired against the real data

- fig3b "Trees are a multi-sector investment" — AC electricity savings plus
  avoided CO₂ at €100/t recover a **median 0.09%** of tree PV cost (max 4.6%,
  Rome; 1.8% for the population-weighted Hot cluster). The panel was dropped
  rather than reframed. Fig 3 is now cost-effectiveness and budget choice.
- fig3c "Greening claws back AC's side-effect" — greening cancels a median
  **2.6%** of the waste-heat penalty, and *least* where the penalty is largest.
  Kept as fig4b, retitled honestly ("Greening offsets little of it").

## Open items

### ⚠️ The greening pathway is confounded by missing cooling coefficients

**This is the one that affects published numbers.** The tree pathway turns
greening into cooling through a per-LCZ, per-month coefficient (`coef_lst` in
`interim/<city>_coef_bridge_check.csv`). Where it is zero, greening yields no
cooling and no avoided deaths regardless of how much canopy is added.

Coverage is sparse and very uneven — share of summer (May–Sep) LCZ-months with a
non-zero coefficient, now carried in `city_metadata.csv` as
`coef_lst_coverage_pct`:

| Coverage | Cities | Median ΔGVI | Median trees avoided /100k | Median € per death |
|---|---|---|---|---|
| < 25% | 9 | 31.3 | 1.80 | €21.4M |
| 25–50% | 21 | 43.6 | 3.84 | €21.2M |
| > 50% | 10 | 30.9 | 6.38 | €7.1M |

Madrid is at **0%** (all 96 LCZ-months zero), which is why it avoids exactly 0
deaths for €458M; Barcelona 5% and Sevilla 9% follow. Bologna tops out at 83%,
median across all 40 is 35%.

It behaves like a coverage artefact specific to greening, not a city trait:

- Spearman **+0.53** with trees avoided deaths /100k, **+0.44** with the benefit
  *per unit of ΔGVI applied*, **−0.48** with log cost per death.
- But only **+0.06** with ΔGVI itself — high-coverage cities do not plant more.
- **Placebo passes**: +0.10 with the AC benefit, +0.03 with EWS.
- **No climate confound**: +0.03 with warm-season mean T2M.

So the cross-city spread in the Trees box of fig1b/c, in fig3a, in fig4c and in
`tab_cba_by_city` is substantially driven by how complete each city's
coefficient bridge is. Fix the bridge upstream before quoting per-city greening
numbers; the AC and EWS pathways are unaffected. `00_city_meta.R` prints a
`[warn]` listing every city below 25%.

### Smaller

- The `masselot_tail` exposure-response variant is identical to the main
  specification in **37 of 40** cities; it binds only in Naples (+0.39%), Rome
  (+0.011%) and Barcelona (+1e-6%). Either widen the tail or drop it from the SI
  — as it stands it is a near-null sensitivity.
- `brussels` is still marked `partial` (failing NB 02) in
  `runs/agnostic_batch/summary.md`, last updated 2026-08-07, but has the full
  23-table output set — the same count as the median city. The batch summary
  looks stale rather than the run incomplete; its results are included.
- The Drive sync-conflict folder `cologne (1)` is still present in the results
  tree. It is filtered at discovery, but deleting it would remove the trap.
- Per-district `*_characteristics.csv` / `trees_*.csv` (bezirk, arrondissement,
  freguesia, …) are still unused; they could support a within-city equity SI.

### Resolved

- **Paris** now has `paris_policy_comparison.csv` (it arrived after the
  2026-08-07 batch summary). It uses the model population (2,318k vs 2,100k in
  config) and contributes to fig2c, which is now 40/40.

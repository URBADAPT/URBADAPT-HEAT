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

Two tiers, so nothing becomes an unreadable 40-bar panel:

1. **Summary figures** (main text) — cross-city, **no per-city panels**. Every
   panel is a distribution or scatter (one point = one city), colored by
   **climate cluster** and using **normalized** metrics (per-100k, per-capita,
   %, shares, slopes). The one exception is `fig1_risk_costs_portfolios` panel b,
   which is deliberately a 40-row heatmap of the standardised outcome profile;
   it only reads as one row per city. The per-city *descriptors* that used to
   sit beside it are now `tab_city_characteristics` instead.
2. **Big city tables** — all cities, city-specific rows (CSV + `\input`-able
   LaTeX). This absorbs the per-city detail.

The exemplar dashboards (one rich single-city panel per cluster medoid) were
**dropped on 2026-08-25**: the per-city detail is carried by the tables, and the
manuscript has no slot for them. `is_exemplar` is still computed in the metadata,
since it costs nothing and identifies the cluster medoids.

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

## LCZ morphology descriptors

`00_city_meta.R` also reads each city's `lcz_masked_fua.tif` — present for all
40 — and reduces it to five columns in `city_metadata.csv`:

| Column | Meaning |
|---|---|
| `lcz_compact_pct` | LCZ 1–3 (compact high/mid/low-rise) as a share **of built land** |
| `lcz_open_pct` | LCZ 4–6 (open high/mid/low-rise), share of built |
| `lcz_other_built_pct` | LCZ 7–10 (lightweight, large low-rise, sparse, industry), share of built |
| `lcz_built_pct` | LCZ 1–10 as a share of the **whole valid FUA** — the built/natural balance |
| `lcz_diversity` | effective number of LCZ classes, `exp(Shannon)` over all valid cells |

The first three sum to 100 (which built form dominates the fabric); the fourth is
the split between the part of the FUA the tree policy can act on and the part it
cannot, since greening is restricted to classes 1–10. Value 0 in the raster is
the nodata fill outside the mask, not a class, and is dropped. The 40 rasters are
the slow part of the metadata build, so they are cached in `.city_lcz_cache.csv`
on the same top-up pattern as the heat metrics (`build_city_meta(force_lcz =
TRUE)` to rebuild).

The gradient is strong and in the expected direction: compact built runs from
0.2% (Nantes) and ~1% across the north to 14.7% in Athens and ~10% in Sevilla,
Barcelona and Madrid.

## Policy archetypes (outcome-based) — and why they mostly restate the climate

> **Superseded for the paper.** The archetypes are no longer shown in any figure
> or quoted in the manuscript; see *Fig 1 absorbed Fig 3, and carries no city
> typology* below for the four variable sets tested and why none was defensible.
> This section documents what `01_city_profiles.R` still computes, which Fig 1
> panel b depends on for its z-scores.

`01_city_profiles.R` groups cities by **what adaptation does there** rather than by
how hot they are, on the 15 variables the manuscript specifies: baseline
mortality per 100k, the three pathway-specific % reductions, the three avoided
deaths per 100k, the three PV costs per capita, the three costs per death, the
pooled public cost share, and the AC waste-heat penalty as a share of AC's gross
benefit. Climate class and the LCZ descriptors are **not** clustering variables —
they are carried through as external annotations. Right-skewed quantities
(mortality, avoided deaths, euros) are log-transformed before standardising, or a
single city at +6 would define the axis.

`k` is chosen by mean silhouette width over k=2…6 (silhouette implemented in the
script rather than adding a `cluster` dependency). Archetypes are ordered by
descending mean baseline mortality, so `A1` is always the highest-risk group.
Output (as of the cleanup): `tables/city_outcome_profiles.csv`, raw features
plus `z_` columns and no archetype column. The per-archetype median table was
deleted.

**Read the diagnostics the script prints before quoting the archetypes.** On the
real 40-city run they say:

- Best k is **2**, at a weak mean silhouette of **0.253** (k=3…6 are all lower,
  0.19–0.22). There is no strong multi-group structure in outcome space.
- The leading outcome axis carries **45%** of the variance and is a
  risk-*magnitude* axis: Spearman **+0.97** with baseline mortality and **+0.86**
  with warm-season mean T2M. Six of the fifteen variables are close to monotone
  in baseline risk, which is itself close to monotone in temperature.
- Consequently **95% of cities are placed by their climate class alone** (A1 = 10
  Hot + 11 Temperate, A2 = 17 Cool + 2 Temperate). The archetypes largely
  *restate* the climate gradient rather than adding an independent typology, so
  the manuscript's framing — outcome-based groups that climate merely describes —
  is not yet supported by this variable list.

If genuinely non-climate archetypes are wanted, the axis to cluster on is the
second principal component (16% of variance), which loads on greening scale and
the public cost share (`pvcap_Trees` 0.47, `av100k_Trees` 0.45, `public_share`
0.45, `red_Trees` 0.41) — i.e. profile *shape* (which lever wins, who pays)
rather than magnitude. That would mean clustering on within-city shares and
ratios instead of levels.

**The greening confound does not reach the archetypes.** Dropping the four
tree-pathway variables and reclustering gives **ARI 1.00 — every city keeps its
group** — and coefficient coverage correlates only **+0.11** with the leading
outcome axis. So the coverage artefact described below, which does distort
per-city greening numbers, does *not* determine archetype membership.

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
Rscript build_all.R          # metadata -> profiles -> figures -> SI -> tables
Rscript fig1_risk_costs_portfolios.R   # or individual steps
```

Requires R (tested 4.5.3): ggplot2, dplyr, tidyr, readr, jsonlite, stringr,
forcats, scales, patchwork, ggrepel, xtable, ncdf4, yaml.

## Fig 1 absorbed Fig 3, and carries no city typology

Two decisions taken on 2026-08-25, both worth knowing before editing Fig 1:

**Fig 3 was retired into Fig 1.** They overlapped almost entirely: old fig3a is
now panel c (stratified by climate class rather than pooled), old fig3b is
panel d, and old fig3c was already byte-for-byte the same plot as Fig 1's
deployment panel. `fig3_costeffectiveness.R` is deleted; Fig 1 now has five
panels a-e. Main-text figures are therefore fig1, fig2 and fig4 — fig4 will
want renumbering to Fig 3 in the manuscript.

**No city typology is presented.** The A1/A2 archetype grouping was dropped
from the figure because no variable set produced a defensible partition. Four
were tested on the real 40-city data:

| Variable set | Best k | Silhouette | PC1 ρ with T2M | PC1 ρ with coef coverage | Placed by climate alone |
|---|---|---|---|---|---|
| Magnitude (the manuscript's 15) | 2 | 0.253 | +0.86 | +0.11 | 95% |
| Scale-free response profile (10) | 3 | 0.189 | +0.05 | −0.47 | 65% |
| Policy mix only (5) | 6 | 0.281 | −0.16 | −0.41 | 42% |
| Scale-free minus tree vars (7) | 2 | 0.323 | +0.05 | +0.28 | 88% |

Silhouette below 0.25 conventionally means no substantial structure; nothing
here clears 0.33, and the 0.323 case is a 5-vs-35 outlier split. Going
scale-free does remove the climate tautology (ρ with temperature falls from
+0.86 to −0.16) but replaces it with the greening-coefficient-coverage
artefact, and ARI between the full and tree-dropped scale-free sets is only
0.39 — i.e. the confound determines membership there. **Revisit the policy-mix
design once the coefficient bridge is fixed**; it is the conceptually right
definition and only the data is blocking it.

`01_city_profiles.R` still runs, because Fig 1 panel b needs the standardised
z-scores it computes and its diagnostics are the evidence for the sentence
above. Its `archetype` column is simply unused by the figure.

## Fig 3 panel c: two swaps, and why

Panel c has been replaced twice, both times because the effect on display was
real but had no cross-city structure to plot.

**First swap.** It originally showed greening alone against greening on top of
AC. That erosion is small (median 7.6%), and EWS appeared nowhere in a figure
about lever interaction, so it became the EWS-AC overlap: existing cooling
already covers a median **24.0%** of the warning system's gross benefit, from
`annual_heat_deaths_avoided_EWS_<city>.csv` (central scenario, `gross_` vs
`net_avoided_deaths`, summed over the four anchor years).

**Second swap.** That 24% turned out to be almost invariant: **22.5-24.9%**
across all 40 cities over an AC coverage range of 14-71%, Spearman only +0.36
with coverage, growing just +1.2 pp within a city from 2020 to 2050. Large, but
it draws as a flat band. It is now stated in the caption text instead.

**What panel c shows now.** Deaths added by AC waste heat against deaths avoided
by the whole greening programme, log-log with a 1:1 line. Above the line the
private lever costs more lives than the public one saves: **7 of the 31 cities**
with usable greening coefficients, median ratio 0.48, max 4.9, and Spearman
**+0.55** with warm-season temperature, so the panel has a real gradient.

The greening side is the confounded quantity, so cities below `COEF_MIN_PCT`
(25%) coefficient coverage are **excluded** from the panel — 6 of them, leaving
31. They were originally drawn as hollow points, but the shape legend was
dropped for simplicity (2026-08-25) and an undistinguished point would present
an artefact as a finding: Barcelona at 5% coverage returns a ratio of 17.6 that
is a property of the coefficient bridge, not of Barcelona. Restricting to the
well-covered cities drops the correlation with coverage to -0.10 while leaving
the temperature gradient intact at +0.55. Outlier labels are additionally
restricted to cities with at least 5 penalty deaths, so Copenhagen's ratio of
1.7 on 1.1 against 0.6 deaths is not headlined next to Athens' 369 against 83.

Panel a's fit annotation (`+3.2 pp per °C, R² = 0.35`) was also removed from the
plot on 2026-08-25; the regression line and band stay. The coefficients are now
printed by the build as `[caption] panel a fit: ...` so the caption, which still
quotes them, can be checked against a rebuild.

**A rejected alternative, for the record.** Showing the EWS benefit reducing the
need for AC and trees is not possible: the runs ship standalone policies plus
the two AC-trees interactions, with no "AC on top of EWS" or "trees on top of
EWS" quantity. Two others were measured and set aside: portfolio sub-additivity
(portfolio benefit over the sum of standalone nets is median 1.00 but minimum
0.65, and 21 of 40 cities are exactly additive, which needs explaining first)
and benefit-share against cost-share (warnings deliver a median 52.8% of
portfolio benefit for 0.9% of its cost, a striking number that nonetheless
restates what Fig 1 c-e already establish).

## Fig 2 panel a: why AC is split by component, not public/private

Changed 2026-08-25. Trees and EWS are still split public/private; AC is now
split into **capital / maintenance / electricity**. The reason is that AC's
public share is not an estimated quantity. In the CBA notebook it is:

```python
AC_CAPEX_PUBLIC_SHARE = 0.20  # 20% subsidy
AC_MAINT_PUBLIC_SHARE = 0.0
AC_ELEC_PUBLIC_SHARE  = 0.0
```

so `public_share == 0.20 * capex / total`. Verified across the runs: the implied
rate is **exactly 0.20 in 40 of 40 cities**, and that formula reproduces the
reported share to 2.8e-17. The 9.0-14.9% spread you see across cities is
therefore *only* the spread in the capital fraction of total cost (45.1-74.4%,
median 61.3%) — it carries no information about the cities. Trees and EWS are
hard-coded at 100% public.

**Watch the per-capita denominator.** There are now two or three rows per city
in the long frame, so the denominator is built from `unique(city, cluster,
pop_k)`. Summing `pop_k` over the long frame would count each city two or three
times and silently halve the bars.

### Why the AC bar grows toward colder climates

This is the question the panel now answers visually, and it is an
incremental-accounting effect rather than a statement about cooling:

| | capital | maint | electricity | total | baseline AC | added users/capita |
|---|---|---|---|---|---|---|
| Hot | €346 | €97 | €154 | €591 | 61.4% | 0.081 |
| Temperate | €555 | €157 | €135 | €746 | 39.9% | 0.138 |
| Cool | €816 | €265 | €194 | €1288 | 26.2% | 0.221 |

Cost per capita tracks added users per capita at Spearman **+0.91** and baseline
2020 coverage at **-0.80**. It does *not* track how far the coverage target
rises (**-0.01**); the Cool class actually has the smallest rise (+4.8 pp against
+6.4 pp for Hot). Cool cities are expensive because they start from a low base,
so the same policy installs ~3x more units per resident. A secondary effect:
`capex_per_user` ranges €964-2,156 across cities (Spearman +0.54 with cost per
capita) and tariffs €0.06-0.30/kWh, both higher in the north.

So the gradient measures *how much cooling remains to be installed*, not the
cost of cooling where it is needed. Read it against Fig 1c, where cool cities
also have the worst cost per life saved (€27.5M against €1.8M in Hot).

**Open item:** the 20% subsidy rate is not in the SI methods and has no
sensitivity run, yet the public--private divide is in the paper's title. Worth a
panel in `si1_sensitivity` sweeping it.

## Reading Fig 1 panel b: what the shading actually encodes

Three things about the heatmap that are not self-evident from looking at it, all
stated in the manuscript caption:

**Red is always the less favourable direction.** Each variable carries a
`worse_high` flag in `PROFILE_FEATURES`. Cost, risk and burden variables are
plotted as-is; the benefit variables (`red_*` mortality reduction, `av100k_*`
avoided deaths) are **sign-reversed for display**, so a larger benefit reads
blue. Before this, the same red meant a bigger benefit in one column and a
bigger cost in the next, which is worse than no colour coding at all. The
stored `z_` columns in `city_outcome_profiles.csv` are **not** flipped — the
reversal happens at plot time only, so the CSV stays numerically honest.

The burden variable is carried as the **private** cost share, not the public
one, for the same reason: a larger private share is unambiguously the paper's
equity concern, whereas "high public share" is favourable or not depending on
the lens.

**Ten of the fifteen variables are standardised on log10.** Mortality, avoided
deaths and every monetary amount span three orders of magnitude across the 40
cities, so their shading is in log space. A city 10x the mean and one 2x the
mean do not differ proportionally in colour.

**Two cells are blanked and marked with a cross.** The cost per avoided death of
greening is undefined in Madrid and Sevilla, which avoid essentially no deaths;
`profile_matrix()` censors those at the sample maximum so k-means could run, and
the raw column is `NA` wherever that happened. The figure detects that and draws
nothing rather than presenting an imputed value as a measurement. They are
cross-marked because a blank tile is white and the diverging midpoint is
near-white, so an unmeasured cell would otherwise read as a merely average one.

Shading saturates at `|z| = 2.5` (`Z_LIMITS`), which clips 17 of the 600 cells;
the true range is -3.6 to +3.5. The build prints all of these counts on the
`[caption]` line so a rebuild on new data shows when the caption has drifted.

## Naming after the 2026-08-25 cleanup

Retired items were removed rather than left as dead numbering, so nothing in the
tree implies a figure that no longer exists:

| Was | Now |
|---|---|
| `fig1_policy_archetypes` | `fig1_risk_costs_portfolios` (no archetypes to name) |
| `fig1_risk_effectiveness` | **deleted** — its panel a is Fig 1a, its pathway boxes duplicate columns of Fig 1b |
| `fig3_costeffectiveness` | **deleted** — absorbed into Fig 1 panels c, d, e |
| `fig4_synergies` | `fig3_synergies` (Fig 3 slot, `fig:result3`) |
| `01_archetypes.R` | `01_city_profiles.R` |
| `city_archetypes.csv` | `city_outcome_profiles.csv` (no `archetype` column) |
| `tab_archetype_profile` | **deleted** |
| `exemplars.R` | **deleted** |

`01_city_profiles.R` now only builds the standardised profile matrix. The
clustering code survives below an explicit opt-in banner as
`cluster_diagnostics()`, which the build never calls; it exists so the
"no defensible typology" claim in the Fig 1 caption stays reproducible.

Main text is Fig 1, Fig 2, Fig 3. SI figures si1-si3, SI table
`tab_city_characteristics`, plus the four big city tables.

## Items

| Script | Output | Slot | Content |
|--------|--------|------|---------|
| `00_city_meta.R` | `tables/city_metadata.csv` | — | Climate metrics + clusters + exemplars + LCZ composition |
| `01_city_profiles.R` | `tables/city_outcome_profiles.csv` | — | Standardised cross-city outcome profiles (the z-scores Fig 1b plots) |
| `fig1_risk_costs_portfolios.R` | `fig1_risk_costs_portfolios` | `fig:result1` | **(a)** baseline risk across the climate gradient; **(b)** standardised outcome profile per city + climate strip; **(c)** € per death by pathway × climate class, pooled medians annotated; **(d)** normalised efficiency frontier; **(e)** portfolio composition as the per-capita budget rises. Absorbed the whole of the former fig3 |
| `fig2_distribution.R` | `fig2_distribution` | `fig:result2` | Public vs private cost/capita by lever (facet=cluster); equity–efficiency trade-off of targeting; greening progressivity |
| `fig3_synergies.R` | `fig3_synergies` | `fig:result3` | AC's waste-heat self-penalty vs heat; how little greening offsets it; **(c)** share of the warning benefit already covered by existing AC, against AC coverage |
| `si1_sensitivity.R` | `si1_sensitivity` | SI | Discount rate; greening cost assumptions; greening ambition |
| `si2_if_comparison.R` | `si2_if_comparison` | SI | Four exposure-response functions and what the choice does to baseline mortality |
| `si3_ews.R` | `si3_ews` | SI | Warning days to 2050; share of deaths on warned days; benefit ramp |
| `tab_cba_by_city.R` | `tab_cba_by_city` | table | CBA by city × pathway (120 rows) |
| `tab_risk_by_city.R` | `tab_risk_by_city` | table | Hazard/exposure/risk by city |
| `tab_ews_by_city.R` | `tab_ews_by_city` | table | Warning days, avoided deaths, cost per death, attribution wording |
| `tab_synergies_by_city.R` | `tab_synergies_by_city` | table | Waste-heat penalty, penalty removed by trees, lever overlap |
| `tab_city_characteristics.R` | `tab_city_characteristics` | SI table | Per-city climate, greening-coefficient coverage and LCZ composition (longtable) |
| `make_report.R` | `report.html` | — | One browsable page with every figure and table |

## Figure styling: no panel titles, everything in the caption

As of 2026-08-25 the seven figures embedded in the manuscript carry **no panel
titles or subtitles**. Nature style puts all description in the caption, and the
captions now live in `manuscript_NATCITIES.tex`. Two consequences worth knowing:

- Panel tags (`a`, `b`, `c`, ...) are still drawn by ggplot via `labs(tag=)`.
- A statistic that belongs to a single panel stays **inside** that panel as an
  `annotate()` label rather than moving to the caption, because a caption number
  goes stale silently on the next rebuild. This applies to the two fitted slopes:
  fig1a (`x1.58 per °C, R² = 0.69`) and fig4a (`+3.2 pp per °C, R² = 0.35`).
- Every other number the captions quote is `message()`d by the build as a
  `[caption]` line. Diff those against the LaTeX after any rebuild:

```
[caption] 15 clustering variables, 2 archetypes; 95% of cities placed by climate class alone; fit x1.58 per °C  (R² = 0.69)
[caption] targeting shifts a median +15.2 pp of benefit to the 2 most vulnerable quintiles for 4.8% of lives saved; equity greening steepens the SVI slope in 18 of 40 cities
[caption] 2 city-pathway(s) with no measurable benefit omitted
[caption] penalty cancelled by greening: median 2.6%; greening loses a median 7.6% of its benefit once AC is already deployed
[caption] the masselot tail extension binds in 3 of 40 cities (max 0.4%)
```

`fig1_risk_effectiveness` is **not** restyled and not embedded: it is superseded
by `fig1_risk_costs_portfolios` and kept only for browsing in `report.html`.

## SI tables: all five are longtables, and must not be wrapped in a float

As of 2026-08-25 every table the pipeline writes is a **longtable** with a
repeating header, not a `tabular` inside a `table` float. A 40-row table (120
for `tab_cba_by_city`) cannot break across pages inside a float, so the LaTeX
side must `\input` them bare:

```latex
{\singlespacing
\input{tables/tab_risk_by_city}
\input{tables/tab_cba_by_city}
\input{tables/tab_ews_by_city}
\input{tables/tab_synergies_by_city}
}
```

Each carries its own `\caption` and `\label` (`tab:risk_by_city`,
`tab:cba_by_city`, `tab:ews_by_city`, `tab:synergies_by_city`,
`tab:city_characteristics`). The `\singlespacing` group matters: the manuscript
is `\doublespacing`, which otherwise doubles every row of a 120-row table.

`save_table()` takes `size =` for this, passed through to xtable. The wide
tables are set smaller because their headers overflow a 17 cm text block at
normal size:

| Table | Rows | Cols | Size |
|---|---|---|---|
| `tab_risk_by_city` | 40 | 8 | `ootnotesize` |
| `tab_cba_by_city` | 120 | 8 | `ootnotesize` |
| `tab_ews_by_city` | 40 | 10 | `\scriptsize` |
| `tab_synergies_by_city` | 40 | 9 | `\scriptsize` |
| `tab_city_characteristics` | 40 | 12 | `\scriptsize` |

**These are copies once they reach the manuscript.** `manuscripts/tables/` holds
duplicates of the five, alongside three that live only in the Overleaf project
(`ews_taxonomy_table`, `liu_benchmark_table`, `uncertainty_matrix`). After any
rebuild the five must be re-copied to `manuscripts/tables/` and re-uploaded to
the Overleaf project's `tables/` folder, which currently holds only the other
three.

## Embedding in the manuscript

The seven figures are `\includegraphics`'d from `figures/<name>.pdf` in
`manuscript_NATCITIES.tex`, all parked as `[p]` floats at the end of the
Supplementary Information under a `PROVISIONAL PLACEMENT` comment banner, with
labels `fig:si_*`. They are meant to be moved into Results/SI by hand and their
labels reconciled with `fig:result1-3`. The PDFs are committed to the Overleaf
project's `figures/` folder; re-upload them there after any rebuild, or the
document will fail on a missing file (the extension is explicit, so LaTeX cannot
silently substitute an older `.png` of the same name).

## Browsing the output

`build_all.R` finishes by writing **`report.html`** — every figure and all five
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
- **Outcome-based archetypes collapse to two groups that climate already
  explains** — best k = 2 at silhouette 0.253, and 95% of cities are placed by
  their climate class alone. The two groups do differ in morphology (median
  compact-built share 4.5% in the high-risk A1 against 1.4% in A2, built share
  37.9% against 29.5%), which is the LCZ story the manuscript wants, but they
  are not an independent typology. See the archetype section above.

### Panels retired against the real data

- fig3b "Trees are a multi-sector investment" — AC electricity savings plus
  avoided CO₂ at €100/t recover a **median 0.09%** of tree PV cost (max 4.6%,
  Rome; 1.8% for the population-weighted Hot cluster). The panel was dropped
  rather than reframed. Those panels are gone; what remained of fig3 is now folded into Fig 1.
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
| < 25% | 8 | 32.8 | 1.38 | €17.9M |
| 25–50% | 22 | 43.5 | 3.83 | €22.9M |
| > 50% | 10 | 30.9 | 6.38 | €7.1M |

Boundaries are strict at the low end and inclusive at the high end, matching the
`< 25` test `00_city_meta.R` warns on: Lisbon sits at exactly 25.0% and belongs
to the middle bucket, Zagreb and Naples at exactly 50.0% likewise.

Madrid is at **0%** (all 96 LCZ-months zero), which is why it avoids exactly 0
deaths for €458M; Barcelona 5% and Sevilla 9% follow. Bologna tops out at 83%,
median across all 40 is 35%.

It behaves like a coverage artefact specific to greening, not a city trait:

- Spearman **+0.53** with trees avoided deaths /100k, **+0.44** with the benefit
  *per unit of ΔGVI applied*, **−0.48** with log cost per death.
- But only **+0.06** with ΔGVI itself — high-coverage cities do not plant more.
- **Placebo passes**: +0.10 with the AC benefit, +0.03 with EWS.
- **No climate confound**: +0.03 with warm-season mean T2M.

So the cross-city spread in the Trees boxes of `fig1_risk_effectiveness`, in
fig1c/d/e, in fig4c and in `tab_cba_by_city` is substantially driven by how complete
each city's coefficient bridge is. Fix the bridge upstream before quoting
per-city greening numbers; the AC and EWS pathways are unaffected.
`00_city_meta.R` prints a `[warn]` listing every city below 25%.

It does **not**, however, reach the policy archetypes: reclustering without the
four tree variables leaves every city in its group (ARI 1.00), and coverage
correlates only +0.11 with the leading outcome axis. The archetype description
in the manuscript is therefore not blocked on fixing the bridge.

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

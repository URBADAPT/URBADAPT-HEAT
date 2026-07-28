# Synthetic 40-city dataset (figure-planning only)

> **These are fake numbers.** They exist so the cross-city figures can be
> designed and revised at their intended ~40-city scale *before* the real model
> runs for all cities land. Never cite, publish, or commit any value here as a
> result. Branch: `synthetic-40-cities`.

## What it does

`R/gen_synthetic_cities.R` fabricates internally-consistent, climate-correlated
placeholder results for every city in `urban-heat/configs/`, spanning the real
European heat gradient (Seville/Athens hot → Helsinki/Dublin cool). This gives
k-means three populated clusters and fills every panel of `fig1`–`fig3`, the
exemplar dashboards, and both big tables.

The two cities with **real** results (Milan, Rome) are **copied in verbatim** as
a magnitude sanity-check next to the synthetic ones — they are *not* faked.

## Full isolation from the real pipeline

Nothing here can touch real data or real outputs:

| | Real pipeline (`build_all.R`) | Synthetic (`build_synthetic.R`) |
|---|---|---|
| Input variant | `outputs_variants/masselot_main_agnostic/` | `outputs_variants/masselot_main_agnostic_synthetic/` |
| Figures out | `figures/` | `figures_synthetic/` |
| Tables out | `tables/` | `tables_synthetic/` |

The split is driven by three env vars (`NATCITIES_VARIANT`, `NATCITIES_FIG_DIR`,
`NATCITIES_TAB_DIR`) that `build_synthetic.R` sets before sourcing `_helpers.R`.
The generator hard-refuses to write into the real variant. Every generated city
dir carries a `.SYNTHETIC` marker and is listed in
`tables_synthetic/.synthetic_manifest.csv`.

The only change to shared code is that `_helpers.R` now reads `FIG_DIR`/`TAB_DIR`
from those env vars, defaulting to the original paths (fully backward compatible).

## Run / clean (PowerShell — see the ncdf/PowerShell gotcha)

```powershell
cd natcities_visual_items/R
Rscript build_synthetic.R   # generate + render everything to *_synthetic dirs
Rscript clean_synthetic.R   # delete the synthetic variant + *_synthetic dirs
```

## How the numbers are built

Per city, from warm-season mean T2M (`W`), hot-day count (`H`) and population
(`P`, thousands) plus a deterministic per-city RNG seed (so runs are stable):

- baseline mortality/100k rises non-linearly with `W`; grows ~18%/decade to 2050
- pathway effectiveness: Trees ~1–2.6%, AC gross ~3–9%, waste-heat erosion 5–34%
  (both rising with `W`), EWS ~4–6%
- costs per capita: Trees ~€150, AC ~€620+ (public share ~5–22%), EWS ~€15
- district greening: uniform slope on SVI ≈ 0, equity slope > 0 (progressive)
- frontier: concave benefit vs budget, scaled by pop and heat
- `cba_summary.json` carries the exact keys the figures read

City climate placements and populations live in the `PROFILES` table at the top
of `gen_synthetic_cities.R` — edit there to reshape the gradient.

## Figure redesign (2026-07-22, Nature Cities framing)

fig2 and fig3 were rebuilt to foreground the paper's thesis — *synergies and
trade-offs of public vs private adaptation*:

- **fig2 — public vs private & justice.** (a) public/private PV cost per capita
  by lever, faceted by cluster (AC = mostly private out-of-pocket; trees/EWS =
  collective); (b) equity-vs-efficiency (income-targeted vs uniform, 1:1 line);
  (c) greening progressivity slope (ΔGVI on SVI).
- **fig3 — synergies & trade-offs.** (a) AC mortality *ledger*: gross indoor
  cooling split into net lives saved vs lives added back by AC's own waste heat
  (stacked; % lost labelled, rising with heat); (b) trees as a cross-sector
  investment: electricity + avoided-CO₂ co-benefits as % of tree cost recovered;
  (c) share of AC's waste-heat penalty cancelled by urban greening.

These need extra CBA-JSON fields (`benefits.ac_with_trees_interaction`,
`benefits.trees.on_top_of_ac_25y`, `vegetation_feedbacks.*`,
`lambda_y_waste_heat.*`) — all present in the real per-city
`<city>_cba_summary.json`, and mirrored in the synthetic generator.
`fig3` panel (b) uses `CARBON_PRICE` (€/t, top of `fig3_synergies.R`) to value CO₂.

## Still-open revision candidates

- fig3 (b) co-benefit recovery is only ~1–2% of tree cost (honest to the data;
  electricity dominates, CO₂ sliver is small) — revisit framing if it reads as
  underwhelming, or add the mortality co-benefit as a separate encoding.
- With 40 points, cluster **overplotting** in the boxplot/scatter panels is
  moderate; consider seeded jitter / smaller points / `ggbeeswarm`.
- `pop_k` for the real cities still comes from configs (Rome had none → set to a
  placeholder here); the real run needs a proper FUA-population source.
- Cluster panels aggregate **population-weighted**; decide whether to also show
  per-city spread (dots) on the fig3 bars.

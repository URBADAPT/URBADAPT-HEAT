# Income emulator — integration + testing update 

*2026-07-01*

The integration into the workflow is done and solid; First attempt at the
built-form/failure-mode fix (the covariate route) came back **negative**; and then the **training-data route - adding French + German cities, including an
eastern one - actually moved the failing zones the right way**. Details below.

## 1. Integration into the workflow (05_AC)

- The `income.source: observed | emulator` switch is wired into 05_AC. `observed` reproduces our
  pilot results exactly; `emulator` feeds the emulator's per-zone `p_inc` as the income input and
  leaves the percentile-rank + AC sigmoid untouched. Because the emulator's `subcity_code` *is* the
  boundary the AC step keys on (Rome = CAP, etc.), it's a clean drop-in. The `aggregation=mean` fix
  is in (the index is already a per-zone mean; the IRPEF path's pop-weighted total divided it by
  population).
- **New since last time — a robustness fix.** 05 was keying the emulator income with
  `to_cap5(subcity_code)` regardless of the city's `match_by`; for name-keyed cities (Copenhagen
  districts) that gives `to_cap5(name) → NaN`, so every zone silently fell back to the city mean
  (flat income, no AC gradient). I moved the join into a small **tested module**
  (`cityheat/income_source.py`) that routes the key by `match_by` (code → `to_cap5`, name →
  normalized name) and added a **loud coverage assert** that fails if fewer than `min_coverage` of
  AC zones match. Observed-mode is provably unchanged (identical expressions), with unit + regression
  tests.
- I ran 05 **end-to-end in emulator mode for Rome**: AC penetration `k*` comes out identical to
  observed (1.39 / 1.51), and each run drops an `income_provenance_<city>.json` (source, n matched,
  match-rate). (72/118 Rome CAPs are income-covered; the rest city-mean-fill.)

## 2. Testing / validation

- Reproduced pipeline: leave-one-country-out zone-weighted Spearman **~0.60**, matches the
  committed run.
- Held-out cities (leave-one-city-out): **Rome 0.72, Athens 0.63, Lisbon 0.51** — the pilots we
  actually downscale. Rome observed-vs-emulated end-to-end through the AC step: maps agree **~0.59**,
  disagreement in the dense historic centre.
- **New — a held-out diagnostic with real ground truth.** I pulled observed sub-city income for
  **Leipzig** (63 Ortsteile, 2023 citizen-survey, from the open data) and deployed with Leipzig held
  out — so we can *measure* the failure modes, not just eyeball them. Baseline (no German training):
  **Spearman 0.18**, and the two failure modes are quantitative and exactly as suspected —
  Gründerzeit/historic core **under-ranked** (mean rank error −0.50; Zentrum-West, the richest,
  predicted ~3rd percentile) and Grünau Plattenbau **over-ranked** (+0.22…+0.38).

## 3. First fix attempt — the covariate route (negative, but instructive)

First tried to give the model the missing built-form/era signal: **building age**
(a GHS building-age raster → per-zone mean — note the `built_age_index` in `subdivisions.csv` is a
synthetic placeholder, so I used the real raster) + a transferable **East/West region flag**, and I
looked at the **sprawl** idea (building density by LCZ) and centrality-type covariates.

=> Overfitting trap: these lifted the CV but **hurt the held-out deployment** —
Munich's objective Spearman dropped from **~0.69 to ~0.60**. The reason is the distribution shift —
Germany is never a CV fold, so any skill a new covariate buys is in-distribution and doesn't transfer
to the German cities we care about. So I kept them **dormant** (production unchanged) and concluded:
**the lever is training data, not features.**

## 4. The training-data route — adding French + German cities (the fix)

Worked on the Eastern-city suggestion and filled the "no German data" gap. Added the canonical
way (a block each in `build_income_subcity_harmonized.R`, PPP/inflation-harmonised like the rest,
join-verified boundaries in `boundaries/processed/`, then rebuilt covariates + retrained):

- **France — Strasbourg (103 IRIS) + Bordeaux (88 IRIS)**, FiLoSoFi 2018 declared income/UC,
  re-filtered from the raw national base + IRIS geometries (same-country cities, to test if they lift
  France itself).
- **Munich — 25 Stadtbezirke**, Einkommensteuerstatistik 2020 (tax).
- **Hamburg — 99 Stadtteile**, Statistik Nord Stadtteil-Profile *Einkommen je Steuerpflichtigen* 2021
  (tax).
- **Dresden — 17 Stadträume**, KBU equivalised household income 2024 (opendata.dresden.de). This is
  the **eastern one** — same Gründerzeit-vs-Plattenbau contrast as Leipzig (Blasewitz/Striesen rich,
  Prohlis/Gorbitz poor). Its income was open but the boundary wasn't published, so I **built the 17
  Stadträume from OSM** (dissolved the 61 Stadtteile using the Stadtbezirk hierarchy for the
  "StB … *ohne* …" carve-outs; verified 17/17 against the income geography).

That takes the ground-truth DB to **157 cities / 16 countries**.

### Results — CV (vs pre-German baseline)

Leave-one-country-out **0.597 → 0.623**, driven mostly by **France 0.51 → 0.61** (same-country cities
help), with new **DE = 0.587** held out as a whole country from scratch. Leave-one-city-out held at
**~0.68**; within-city bottom-quintile tail-hit **0.54 → 0.59**. Small concept-mixing dips on tiny
countries (DK 9 zones, CH 34 — un-evaluable). So a **real but modest** overall gain, no regression —
the "decent approximation functional to URBADAPT" level. 

### Results — the targeted test (held-out Leipzig)

| training | Spearman | historic-core err | Plattenbau err |
|---|---|---|---|
| baseline (no German) | 0.18 | −0.50 | +0.30 |
| + Munich/Hamburg/Strasbourg/Bordeaux | 0.34 | −0.41 | +0.05 |
| + Dresden (eastern) | 0.42 | −0.36 | +0.08 |

Two things I didn't expect:

1. **Plattenbau over-ranking was a *calibration* problem, not a missing-morphology one.** Just having
   German cities in training (Munich/Hamburg — both western, *no* Plattenbau) re-anchored the
   built-form→income mapping and largely fixed it (+0.30 → +0.05). We didn't need Plattenbau examples
   for that half.
2. **The Gründerzeit/historic-core under-ranking is what needs in-region eastern examples.** Dresden
   (same contrast as Leipzig) shrinks it further (−0.41 → −0.36) and lifts overall skill (0.34 →
   0.42), monotonically. Not fully closed yet (Zentrum-West still under-ranked), so more eastern
   cities would continue it.

On **Berlin**: the income exists (Bruttomedianentgelt at 542 LOR) but isn't openly downloadable —
it's in the BA portal / interactive atlas, not a clean table (I checked the open-data API), so I
couldn't add it cleanly; boundaries there aren't the blocker. Poland/Czech sub-city income is
genuinely hard (published at powiat/gmina, not intra-city).

## Net

Integration is clean and reproduces observed exactly; the failure modes are now *measured* on a
held-out eastern city; the covariate route overfits; and adding cities — especially
eastern **Dresden** — is what actually fixes the failing zones. 

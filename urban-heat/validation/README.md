# Baseline validation

Answers the reviewer question *"how close is the modelled baseline to real values, if and
where those are available?"* for the Nature Cities manuscript.

## Run order

```bash
python validation/fetch_benchmarks.py          # independent series -> benchmarks/
python validation/compare_baseline.py          # three-way comparison -> outputs/
python validation/diagnose_baseline.py         # fault localisation -> outputs/
```

`compare_baseline.py` and `diagnose_baseline.py` read the cluster run from Drive; pass
`--outputs-base` / `--aux-base` to point elsewhere.

## Three things to know before reading the numbers

**1. The modelled "2020" is a climatology, not the year 2020.** Every city config sets
`t2m_baseline_mode_default: climatology_mean` over UrbClim 2008-2017. Comparing against
observed 2020 mortality would be a category error, and 2020 is a COVID year besides. All
benchmarks here are therefore multi-year means.

**2. There is no observed "real value" to validate against.** Heat-attributable deaths
are never measured; every benchmark is itself a model. What differs is the
counterfactual:

| Counterfactual | Sources | Usable? |
| --- | --- | --- |
| Minimum-mortality temperature | Masselot, Sante publique France, **us** | yes, like-for-like |
| Threshold / expected baseline | Spain MoMo | yes, but conservative - compare ratios |
| Recent-years seasonal norm | Italy SISMG, EuroMOMO | **no** |

The last row is the trap. SISMG's "expected" is fitted on recent years and so already
absorbs a typical summer's heat; it reported summer 2025 at **-4% vs expected** against a
+1.72 C anomaly. Its excess measures the anomaly on top of heat, not heat. This rules out
the only city-level Italian series, which costs us Rome, Milan, Naples, Palermo, Bologna.

**3. Benchmark geographies are bigger and cooler than the modelled FUA.** Departement and
province include rural hinterland, so a benchmark rate is normally a *lower bound* on the
city rate. `model_over_ref` slightly above 1 is the expected result; below 1 is a red flag.
`reference_geographies.csv` grades each match, and Paris is the one close match in the
sample (dept 75 at 2.13M against a modelled 2.32M).

## What the current run (n128_results) shows

**vs Masselot, all 40 cities** - a reproduction check, since we use their coefficients:
the cross-city gradient holds (Spearman 0.85 on rates) but levels do not (17/40 within a
factor of 2), and the error is graded in temperature (log rate ratio vs annual mean T,
r = +0.66, ~x1.3 per degC). Cool cities far too low, hot cities too high.

**vs independent sources** - Paris 1.23, Marseille 1.72, Lyon 3.18, Nantes **0.10**
against Sante publique France. The `mas_over_ref` column shows Masselot sits at 0.81-1.59
against the same benchmark, so the definitional offset is small and the deviation is ours.
Paris passing within ~23% on the sample's cleanest geographic match is the single
strongest validation result available.

**Two faults, not one.** Domain coverage explains the hot-city inflation (Spearman -0.53
between coverage and the rate ratio; the over-predicted cities are those whose exposure
population falls well short of the URAU). Age inversion is a *separate* and narrower
fault: five north-west maritime cities put most of their heat deaths in 15-64 rather than
65+, but the correlation with under-prediction overall is only +0.16, so Dublin (0.03x)
and Stockholm (0.14x) still need their own explanation. Athens is an outlier on every
cut - 168 deaths/100k on 29% of the URAU population - and reads as a domain error rather
than a bias.

## Not used, and why

- **Ballester / Gallo / Janos** (Nature Medicine 2023/2024/2025) would cover all 40 cities
  at NUTS-3, but publish code only, no per-region estimate table. Using them means
  re-running their pipeline on Eurostat weekly deaths.
- **EuroMOMO / Eurostat weekly deaths** are genuinely observational but all-cause, and
  carry the seasonal-norm counterfactual described above.

## Files

| Path | Contents |
| --- | --- |
| `reference_geographies.csv` | city slug -> URAU, benchmark geography, NUTS-3, match grade |
| `fetch_benchmarks.py` | downloads SpF + MoMo, reduces to annual totals |
| `benchmarks/*_annual.csv` | tracked annual aggregates |
| `compare_baseline.py` | model vs Masselot vs independent sources |
| `diagnose_baseline.py` | age inversion and domain coverage faults |
| `outputs/` | generated comparison + diagnostic tables (not tracked: `outputs/` is a repo-wide build-product ignore) |
| `cache/` | raw upstream payloads, gitignored |

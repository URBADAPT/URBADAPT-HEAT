"""Quantify the temperature-support difference between UrbClim and Masselot's ERA5-Land.

Raised by a co-author: upper-tail *structural* uncertainty is already represented by two
Masselot variants (constant extrapolation beyond each city's ERA5-Land maximum, and a
log-linear RR extrapolation fitted on the last ~1 C of the curve, which the deterministic
specification uses). What was never done is bias-correcting UrbClim onto the ERA5-Land
distribution the exposure-response functions were fitted on. The proposal was to measure
the support difference before deciding whether it needs a sensitivity. This does that.

Three distributions per city, on the same daily-mean footing:

  era5_masselot   Masselot's own ERA5-Land daily-mean distribution for the URAU
                  (`10288665/tmean_distribution.csv`, full 0-100th percentiles). This is
                  the support the impact functions were ESTIMATED on, so it defines where
                  the curve is interpolation and where it is extrapolation.
  urbclim_raw     All observed UrbClim days pooled across 2008-2017, carried onto the
                  production citymask level. What the hazard would see with its
                  variability intact.
  urbclim_clim    The day-of-year climatological mean, also at citymask level. What the
                  production hazard actually sees.

Why all three rather than two. A naive UrbClim-vs-ERA5 comparison conflates two distinct
things: a genuine instrument difference (UrbClim resolves the urban heat island at ~100 m,
ERA5-Land does not, so UrbClim should be warmer over a city) and the climatology operator
that discards variance (quantified separately in test_baseline_mode.py). Only the first is
a support/bias question. Separating them also tests whether the two faults point the same
way - if UrbClim runs warm against the estimation support, that is a candidate explanation
for the ~2x over-prediction that survives the Jensen correction in every city tested.

Reported per city: distribution quantiles, the MMT and how far each distribution's mass
sits above it, and whether each exceeds the ERA5-Land support maximum - the point beyond
which the RR curve is extrapolated rather than fitted.

Usage:
    python validation/compare_temperature_support.py
    python validation/compare_temperature_support.py --cities berlin dublin

Requires the domain-mean caches from test_baseline_mode.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_baseline_mode import AUX, domain_means, find_local_cities  # noqa: E402

URBAN_HEAT = HERE.parent
QUANTILES = [0.50, 0.90, 0.95, 0.99, 1.00]


def era5_distribution(urau: str) -> np.ndarray:
    """Masselot's ERA5-Land daily-mean percentile vector for one URAU."""
    d = pd.read_csv(URBAN_HEAT / "10288665" / "tmean_distribution.csv")
    row = d[d.URAU_CODE == urau]
    if row.empty:
        raise KeyError(urau)
    cols = [c for c in d.columns if c.endswith("%")]
    probs = np.array([float(c[:-1]) / 100 for c in cols])
    vals = row[cols].to_numpy(dtype=float).ravel()
    order = np.argsort(probs)
    return probs[order], vals[order]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cities", nargs="*", default=None)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    local = find_local_cities()
    slugs = [s for s in (args.cities or local) if s in local]
    if not slugs:
        print("[error] no cached UrbClim cities; run test_baseline_mode.py first")
        return 2

    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)
    mm = pd.read_csv(URBAN_HEAT / "10288665" / "results" / "city.csv")[
        ["URAU_CODE", "mmt"]
    ]

    rows = []
    for slug in slugs:
        urau = geo.loc[geo.slug == slug, "urau_code"].iloc[0]
        mmt = float(mm.loc[mm.URAU_CODE == urau, "mmt"].iloc[0])

        dm = domain_means(local[slug], slug, refresh=False)
        clim = dm.mean(axis=1).values
        h = pd.read_csv(AUX / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv")
        h = h[pd.to_datetime(h.date).dt.year == 2020]
        offset = h.mean_intensity_citymask_degC.mean() - clim.mean()

        probs, era = era5_distribution(urau)
        era_max = era[-1]

        series = {
            "era5_masselot": None,          # handled via its percentile vector
            "urbclim_raw": dm.values.ravel() + offset,
            "urbclim_clim": clim + offset,
        }
        for name, vals in series.items():
            if name == "era5_masselot":
                q = {f"p{int(p*100)}": float(np.interp(p, probs, era))
                     for p in QUANTILES[:-1]}
                q["p100"] = float(era_max)
                # Share of days above MMT, read off the percentile curve.
                above = float(1 - np.interp(mmt, era, probs))
                vmax = era_max
            else:
                q = {f"p{int(p*100)}": float(np.quantile(vals, p)) for p in QUANTILES}
                above = float((vals > mmt).mean())
                vmax = float(vals.max())
            rows.append({
                "slug": slug, "dist": name, "mmt": mmt,
                **q,
                "share_days_above_mmt": above,
                "max_minus_mmt": vmax - mmt,
                "max_minus_era5_max": vmax - era_max,
            })

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 240)

    print("[temperature support]  daily-mean degC, citymask level for UrbClim\n")
    show = ["slug", "dist", "p50", "p90", "p95", "p99", "p100",
            "mmt", "share_days_above_mmt", "max_minus_era5_max"]
    print(out[show].to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    piv = out.pivot(index="slug", columns="dist", values="p99")
    pm = out.pivot(index="slug", columns="dist", values="p100")
    sh = out.pivot(index="slug", columns="dist", values="share_days_above_mmt")
    print("\n[summary]  UrbClim minus ERA5-Land (positive = our hazard runs warmer)")
    cmp = pd.DataFrame({
        "p99_raw_minus_era5": piv.urbclim_raw - piv.era5_masselot,
        "p99_clim_minus_era5": piv.urbclim_clim - piv.era5_masselot,
        "max_raw_minus_era5": pm.urbclim_raw - pm.era5_masselot,
        "days>MMT era5": sh.era5_masselot,
        "days>MMT raw": sh.urbclim_raw,
        "days>MMT clim": sh.urbclim_clim,
    })
    print(cmp.to_string(float_format=lambda x: f"{x:,.3f}"))

    print("\n[reading]")
    print("  p99_raw_minus_era5 > 0 means UrbClim's upper tail sits above the support the")
    print("  curve was fitted on, so the deterministic run is extrapolating where Masselot")
    print("  interpolated - and the log-linear variant's slope assumption starts to carry")
    print("  the result. Where it is < 0 the central fields stay inside the fitted range")
    print("  and the tail variants cannot matter deterministically, which is the")
    print("  co-author's point about Dublin.")
    print("  days>MMT compares exposure above the minimum-mortality temperature: this, not")
    print("  the extreme tail, is what sets the integrated burden.")

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / "temperature_support.csv"
    out.to_csv(dest, index=False)
    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

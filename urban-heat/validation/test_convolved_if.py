"""Test a zero-cost alternative to re-running the hazard: convolve the impact functions.

test_baseline_mode.py shows the baseline error is a Jensen gap - the pipeline evaluates
f(mean of the years) where an average year's burden is mean of f(year). The obvious fix
is to carry every observed year through the hazard and average the impacts, but that
multiplies the hazard size and the impact computation by the number of years (10), and
it touches NB02, NB03 and NB04 plus every downstream assumption that a target year has
365 days.

This script tests whether the same correction can be obtained for free, by moving it out
of the hazard and into the impact function.

    mean over years of f(T_clim + eps)  ==  f_tilde(T_clim),  where
    f_tilde(T) = E_eps[ f(T + eps) ]

If the daily anomaly eps = T(day, year) - T_clim(day) is approximately identically
distributed across days and pixels, then f_tilde is a single, well-defined function: the
impact function convolved with the observed anomaly distribution. It can be precomputed
once per city and written into the existing impact-function JSONs. The hazard, the
frequencies, the event counts and every downstream notebook stay exactly as they are,
and the cluster cost is unchanged.

The assumption is the whole question, and it is testable. Anomalies are in fact
heteroscedastic - summer days vary more than winter days in most climates, and the
variance differs by pixel - so this compares the convolved-IF burden against the exact
per-year ensemble burden that test_baseline_mode.py computes. If the two agree closely,
the cheap fix is defensible and the ensemble re-run is unnecessary. If they do not, the
gap is the price of avoiding the re-run, and this script quantifies it.

Usage:
    python validation/test_convolved_if.py
    python validation/test_convolved_if.py --cities dublin rome

Requires: the domain-mean caches written by test_baseline_mode.py (run that first).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_baseline_mode import (  # noqa: E402
    AUX,
    age_weights,
    burden,
    domain_means,
    find_local_cities,
    load_if,
)


def convolve_if(x: np.ndarray, y: np.ndarray, anomalies: np.ndarray) -> np.ndarray:
    """f_tilde(T) = mean over the observed anomaly sample of f(T + eps).

    Evaluated on the same temperature grid as the original curve, so the result is a
    drop-in replacement: same `intensity` axis, corrected `mdd` values.
    """
    # (n_grid, n_anom) would be large; accumulate instead.
    out = np.zeros_like(x, dtype=float)
    for eps in anomalies:
        out += np.interp(x + eps, x, y, left=y[0], right=y[-1])
    return out / len(anomalies)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cities", nargs="*", default=None)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    # A city qualifies if its domain-mean cache exists OR its raw UrbClim does: the
    # cache is the actual input here, and the netCDF is only how the cache gets built.
    # Without the first branch a worktree that holds the caches but not the ~300 MB of
    # netCDF per city silently drops those cities, which is how the check came to cover
    # seven cities while nine were available.
    local = find_local_cities()
    for p in sorted((HERE / "cache").glob("urbclim_*_domainmean.csv")):
        local.setdefault(p.name[len("urbclim_"):-len("_domainmean.csv")], "")
    slugs = [s for s in (args.cities or sorted(local)) if s in local]
    if not slugs:
        print("[error] no cities with cached UrbClim data; run test_baseline_mode.py first")
        return 2

    rows = []
    for slug in slugs:
        dm = domain_means(local[slug], slug, refresh=False)
        clim = dm.mean(axis=1).values

        h = pd.read_csv(AUX / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv")
        h = h[pd.to_datetime(h.date).dt.year == 2020]
        offset = h.mean_intensity_citymask_degC.mean() - clim.mean()

        ifs = load_if(slug)
        w = age_weights(slug)

        # Daily anomalies about the day-of-year mean: the variability the climatology
        # throws away.
        anom2d = dm.values - clim[:, None]          # (365, n_years)
        anom = anom2d.ravel()

        exact = np.mean([
            burden(dm[y].values + offset, ifs, w) for y in dm.columns
        ])
        naive = burden(clim + offset, ifs, w)

        # (a) one convolution for the whole year.
        conv_ifs = {a: (x, convolve_if(x, y, anom)) for a, (x, y) in ifs.items()}
        cheap = burden(clim + offset, conv_ifs, w)

        # (b) a convolution per calendar month. Anomalies are strongly
        # heteroscedastic - summer days vary far more than winter days - and pooling
        # them applies summer-sized excursions to mild days, which in a cool city
        # manufactures exceedance of an MMT the climatology never reaches. Conditioning
        # on month costs nothing extra at runtime: it is still a fixed table per city,
        # just twelve of them.
        doy_month = pd.date_range("2001-01-01", periods=365, freq="D").month.values
        cheap_m = 0.0
        for m in range(1, 13):
            sel = doy_month == m
            am = anom2d[sel].ravel()
            ifs_m = {a: (x, convolve_if(x, y, am)) for a, (x, y) in ifs.items()}
            cheap_m += burden(clim[sel] + offset, ifs_m, w)

        rows.append({
            "slug": slug,
            "anom_sd_c": anom.std(),
            "naive_climatology": naive,
            "convolved_annual": cheap,
            "convolved_monthly": cheap_m,
            "exact_ensemble": exact,
            "annual_over_exact": cheap / exact if exact else np.nan,
            "monthly_over_exact": cheap_m / exact if exact else np.nan,
            "naive_over_exact": naive / exact if exact else np.nan,
        })

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("[convolved impact function vs the exact per-year ensemble]")
    print("  burden is deaths per exposed person per year\n")
    print(out.to_string(index=False, float_format=lambda x: f"{x:,.4g}"))

    for lbl, col in (("annual", "annual_over_exact"), ("monthly", "monthly_over_exact")):
        e = (out[col] - 1).abs()
        print(f"\n  convolved-IF ({lbl}) vs the exact ensemble: "
              f"median {e.median():.1%}, worst {e.max():.1%}")
    print(f"  the uncorrected default is off by: "
          f"median {(1 - out.naive_over_exact).median():.1%} low, "
          f"worst {(1 - out.naive_over_exact).max():.1%} low")
    print("\n  The correction can be baked into the impact functions - no hazard")
    print("  re-run, no change to event counts, frequencies or downstream notebooks -")
    print("  if its residual is small next to the error it removes. The residual is")
    print("  the price of assuming anomalies are identically distributed; conditioning")
    print("  on month relaxes that where it matters most.")

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / "convolved_if_test.csv"
    out.to_csv(dest, index=False)
    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Propagate the variance-corrected impact function through one city's whole result chain.

test_convolved_if.py shows that convolving the impact functions with the month-conditioned
daily anomaly distribution recovers the exact per-year ensemble burden to within ~2%,
without touching the hazard. This script applies that correction to a single city and
reports what changes downstream: baseline deaths, each pathway's percentage reduction,
avoided deaths per 100k, and cost per avoided death.

Why the pathways are not all treated the same way.

  * Trees act by shifting temperature, so the correction does NOT simply scale their
    benefit: a smoother curve has a different slope at the operating point. The model's
    own air-temperature shift is unavailable for most cities (`tree_cooling_C` is NaN
    wherever the LCZ cooling coefficients are missing), so the effective shift is backed
    out from the model's reported avoided deaths under the UNCORRECTED curve, then
    re-applied under the corrected one. That keeps the comparison self-consistent and
    independent of the LST-to-air bridge.

  * AC and EWS act by removing a share of the burden among a covered population, so their
    avoided deaths scale with the size of the pool to first order. Their percentage
    reductions are therefore roughly invariant and their absolute benefits scale with the
    baseline correction. This is an approximation - AC's waste-heat penalty is itself a
    temperature shift - and it is flagged as such in the output.

Costs do not depend on mortality, so present-value costs are unchanged and every change
in cost per avoided death comes from the denominator.

Usage:
    python validation/apply_correction_city.py --city berlin
    python validation/apply_correction_city.py --city berlin --json

Requires the `urbanheat` conda env (xarray/netCDF4) and local UrbClim for the city.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq

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
from test_convolved_if import convolve_if  # noqa: E402

OUTPUTS = Path(
    r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"
)
DOY_MONTH = pd.date_range("2001-01-01", periods=365, freq="D").month.values


def monthly_corrected(ifs: dict, anom2d: np.ndarray) -> dict[int, dict]:
    """One convolved impact-function set per calendar month."""
    out = {}
    for m in range(1, 13):
        am = anom2d[DOY_MONTH == m].ravel()
        out[m] = {a: (x, convolve_if(x, y, am)) for a, (x, y) in ifs.items()}
    return out


def burden_corrected(series: np.ndarray, per_month: dict, w: dict) -> float:
    return sum(
        burden(series[DOY_MONTH == m], per_month[m], w) for m in range(1, 13)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--city", required=True)
    ap.add_argument("--json", action="store_true", help="also dump a machine-readable summary")
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    slug = args.city.lower()
    local = find_local_cities()
    if slug not in local:
        print(f"[error] no local UrbClim for {slug}; available: {sorted(local)}")
        return 2

    # ---- reconstruct the hazard and both curves -------------------------------------
    dm = domain_means(local[slug], slug, refresh=False)
    clim = dm.mean(axis=1).values
    anom2d = dm.values - clim[:, None]

    h = pd.read_csv(AUX / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv")
    h = h[pd.to_datetime(h.date).dt.year == 2020]
    offset = h.mean_intensity_citymask_degC.mean() - clim.mean()
    series = clim + offset

    ifs = load_if(slug)
    w = age_weights(slug)
    per_month = monthly_corrected(ifs, anom2d)

    b_naive = burden(series, ifs, w)
    b_corr = burden_corrected(series, per_month, w)
    b_exact = np.mean([burden(dm[y].values + offset, ifs, w) for y in dm.columns])

    # ---- production numbers for this city -------------------------------------------
    T = OUTPUTS / slug / "tables"
    pop = pd.read_csv(T / f"{slug}_policy_comparison.csv")["population"].sum()
    base = pd.read_csv(T / f"annual_heat_deaths_baseline_current_ac_{slug}.csv")
    base_2020 = float(base.loc[base.year == 2020, "deaths_overall"].iloc[0])
    eff = pd.read_csv(T / f"{slug}_policy_effectiveness.csv")
    cea = pd.read_csv(T / f"{slug}_cea_summary.csv")

    val = pd.read_csv(args.out / "baseline_validation.csv").set_index("slug")
    mas_deaths = float(val.loc[slug, "mas_deaths"])
    mas_lo, mas_hi = float(val.loc[slug, "mas_lo"]), float(val.loc[slug, "mas_hi"])

    # The reconstruction's absolute level is approximate (domain mean + a constant), so
    # the correction is applied as a RATIO to the production baseline rather than as a
    # level. That cancels most of the reconstruction error.
    k_base = b_corr / b_naive if b_naive > 0 else np.inf
    base_corr = base_2020 * k_base if np.isfinite(k_base) else np.nan

    print(f"[{slug}]  offset {offset:+.2f} C | anomaly sd {anom2d.std():.2f} C")
    print(f"  reconstruction: naive {b_naive:.3e}  corrected {b_corr:.3e}  "
          f"exact ensemble {b_exact:.3e}")
    print(f"  corrected/exact = {b_corr / b_exact:.3f}  (how well the cheap fix stands in)")
    print(f"  correction factor on the baseline: x{k_base:.2f}\n")

    print("=== baseline heat deaths, 2020 ===")
    print(f"  production                {base_2020:10.1f}   ({base_2020 / pop * 1e5:6.2f} /100k)")
    print(f"  variance-corrected        {base_corr:10.1f}   ({base_corr / pop * 1e5:6.2f} /100k)")
    print(f"  Masselot (same city)      {mas_deaths:10.1f}   "
          f"[95% eCI {mas_lo:.1f} - {mas_hi:.1f}]")
    print(f"  production / Masselot     {base_2020 / mas_deaths:10.2f}")
    print(f"  corrected  / Masselot     {base_corr / mas_deaths:10.2f}")
    inside = mas_lo <= base_corr <= mas_hi
    print(f"  corrected inside Masselot's interval: {'YES' if inside else 'no'}")

    # ---- trees: a temperature shift, so re-solve rather than scale -------------------
    trees = eff[eff.policy.str.contains("Tree", case=False)].iloc[0]
    trees_avoided_25y = float(trees.avoided_deaths_25y)

    dt_trees = np.nan
    try:
        # The effective air-temperature shift that reproduces the model's own relative
        # tree benefit under the UNCORRECTED curve. Solving for it rather than reading
        # tree_cooling_C keeps this independent of the LST-to-air bridge, which is
        # missing for most cities.
        target = trees_avoided_25y / 25 / base_2020
        dt_trees = brentq(
            lambda dt: (b_naive - burden(series - dt, ifs, w)) / b_naive - target,
            1e-6, 5.0, xtol=1e-6,
        )
    except Exception as exc:
        print(f"\n  [warn] could not back out the tree shift: {type(exc).__name__}")

    rows = []
    for _, r in eff.iterrows():
        name = str(r.policy)
        red = abs(float(r.pct_reduction))
        if "Tree" in name and np.isfinite(dt_trees):
            red_corr = (b_corr - burden_corrected(series - dt_trees, per_month, w)) / b_corr * 100
            mech = f"dT shift {dt_trees:.3f} C, re-solved"
        else:
            red_corr = red      # share-of-pool mechanism: invariant to first order
            mech = "share of pool, assumed invariant"
        avoided_25y = float(r.avoided_deaths_25y)
        avoided_corr = avoided_25y * (red_corr / red) * k_base if red else np.nan
        rows.append({
            "policy": name,
            "pct_reduction": red,
            "pct_reduction_corr": red_corr,
            "per100k_yr": abs(float(r.deaths_per_100k_year)),
            "per100k_yr_corr": abs(float(r.deaths_per_100k_year)) * (red_corr / red) * k_base,
            "avoided_25y": avoided_25y,
            "avoided_25y_corr": avoided_corr,
            "mechanism": mech,
        })
    eff_out = pd.DataFrame(rows)

    pd.set_option("display.width", 220)
    print("\n=== pathway effectiveness ===")
    print(eff_out[["policy", "pct_reduction", "pct_reduction_corr",
                   "per100k_yr", "per100k_yr_corr", "mechanism"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ---- cost per avoided death: costs unchanged, denominator moves ------------------
    cea = cea.copy()
    lut = {r.policy: r.avoided_25y_corr / r.avoided_25y
           for r in eff_out.itertuples() if r.avoided_25y}

    def scale_for(p: str) -> float:
        s = str(p)
        for k, v in lut.items():
            tag = "Tree" if "Tree" in k else ("EWS" if "EWS" in k else "AC")
            if tag.lower() in s.lower():
                return v
        return np.nan

    cea["scale"] = cea.Policy.map(scale_for)
    cea["Avoided_corr"] = cea.Avoided_Deaths_25y * cea.scale
    cea["Cost_per_Death_corr"] = cea.PV_Cost_EUR / cea.Avoided_corr
    print("\n=== cost per avoided death (present value; costs unchanged) ===")
    print(cea[["Policy", "Avoided_Deaths_25y", "Avoided_corr",
               "Cost_per_Death_EUR", "Cost_per_Death_corr"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.0f}"))

    print("\n  Percentage reductions are near-invariant by construction for AC and EWS")
    print("  (share-of-pool); the trees row is re-solved and may move. All absolute")
    print("  benefits scale with the baseline, so every cost-per-death change is the")
    print("  denominator moving.")

    args.out.mkdir(parents=True, exist_ok=True)
    eff_out.to_csv(args.out / f"corrected_chain_{slug}_effectiveness.csv", index=False)
    cea.to_csv(args.out / f"corrected_chain_{slug}_cea.csv", index=False)
    if args.json:
        (args.out / f"corrected_chain_{slug}.json").write_text(json.dumps({
            "slug": slug, "k_base": k_base, "offset_c": offset,
            "baseline_production": base_2020, "baseline_corrected": base_corr,
            "masselot": mas_deaths, "masselot_ci": [mas_lo, mas_hi],
            "corrected_over_exact": b_corr / b_exact,
            "dt_trees_c": dt_trees,
        }, indent=2))
    print(f"\n[write] {args.out}/corrected_chain_{slug}_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

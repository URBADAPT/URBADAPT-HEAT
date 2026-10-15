"""Numbers for the robustness checks on the trees-versus-cooling comparison.

Recomputes, from the run outputs rather than by hand:
  * how the ratio of tree to cooling cost per avoided death relates to baseline greenness
    (population-weighted Green View Index of the greening regions), to coefficient
    coverage and to city size;
  * trees-cheaper counts among the greener and the less green half of the sample;
  * the comparison under the common absolute target (every district raised to the
    sample's highest district GVI), whose present-value cost is the capital cost of
    that target-sensitivity run scaled by the city's baseline ratio of present-value
    cost to nominal capital expenditure.

Usage:
    python tree_robustness_numbers.py
    OUT_TREE=<tables tree> AUX_TREE=<interim tree> META=<city_metadata.csv> python tree_robustness_numbers.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TREE = os.environ.get("OUT_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\ewsfix40")
AUX_TREE = os.environ.get("AUX_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40")
META = os.environ.get("META", os.path.join(HERE, "tables", "city_metadata.csv"))
NO_BENEFIT_DEATHS = 0.5   # matches _helpers.R
COVERAGE_MIN = 25.0       # % of summer LCZ-months with a retained cooling coefficient


def city_row(c: str) -> dict | None:
    tb = os.path.join(OUT_TREE, c, "tables")
    s = json.load(open(os.path.join(tb, f"{c}_cba_summary.json"), encoding="utf-8"))
    tr_pv = s["costs"]["trees"]["pv_total_base"]
    tr_d = s["benefits"]["trees"]["avoided_deaths_25y"]
    ac_cpd = s["costs"]["ac"]["pv_total"] / s["benefits"]["ac"]["net_25y"]
    pc = pd.read_csv(os.path.join(tb, f"{c}_policy_comparison.csv"))
    gvi = float(np.average(pc.GVI_baseline, weights=pc.population))
    ts_path = os.path.join(AUX_TREE, c, "interim", f"{c}_trees_target_sensitivity.csv")
    ts = pd.read_csv(ts_path) if os.path.exists(ts_path) else None
    row = {"city": c, "tr_pv": tr_pv, "tr_d": tr_d, "ac_cpd": ac_cpd, "gvi": gvi}
    if ts is not None:
        base = ts[ts.scenario.str.startswith("Q3 (baseline")].iloc[0]
        top = ts[ts.scenario == "Max observed"].iloc[0]
        pv_per_capex = tr_pv / base.capex_eur
        row.update(top_target=top.target_gvi, top_pv=top.capex_eur * pv_per_capex,
                   top_d=top.avoided_deaths_25y)
    return row


def main() -> int:
    meta = pd.read_csv(META).set_index("city")
    d = pd.DataFrame([city_row(c) for c in meta.index]).set_index("city")
    d = d.join(meta[["coef_lst_coverage_pct", "pop_k"]])
    ok = d.tr_d >= NO_BENEFIT_DEATHS
    d["tr_cpd"] = (d.tr_pv / d.tr_d).where(ok)
    d["ratio"] = d.tr_cpd / d.ac_cpd
    x = d[ok]

    print(f"cities with a defined tree ratio: {len(x)} of {len(d)}")
    print(f"trees cheaper than cooling: {(x.ratio < 1).sum()} -> {sorted(x.index[x.ratio < 1])}")
    targets = d.top_target.dropna().unique()
    print(f"'Max observed' target GVI across cities: {sorted(set(np.round(targets, 2)))}")

    rho = stats.spearmanr(x.ratio, x.gvi)
    cov = x[x.coef_lst_coverage_pct >= COVERAGE_MIN]
    rho_cov = stats.spearmanr(cov.ratio, cov.gvi)
    print(f"\nSpearman(ratio, pop-weighted baseline GVI): {rho[0]:+.2f} (p={rho[1]:.2f}, n={len(x)})")
    print(f"  among cities with coverage >= {COVERAGE_MIN:.0f}%: {rho_cov[0]:+.2f} (p={rho_cov[1]:.2f}, n={len(cov)})")
    med = x.gvi.median()
    green, less = x[x.gvi > med], x[x.gvi <= med]
    print(f"trees cheaper in {(green.ratio < 1).sum()} of the {len(green)} greenest vs "
          f"{(less.ratio < 1).sum()} of the {len(less)} least green (split at median GVI {med:.1f})")
    for lab, col in (("city size (population of the modelled area)", "pop_k"),
                     ("coefficient coverage", "coef_lst_coverage_pct")):
        r = stats.spearmanr(x.ratio, x[col])
        print(f"Spearman(ratio, {lab}): {r[0]:+.2f} (p={r[1]:.3f})")

    t = d[d.top_d >= NO_BENEFIT_DEATHS].copy()
    t["ratio_top"] = (t.top_pv / t.top_d) / t.ac_cpd
    both = t.index.intersection(x.index)
    changed = [c for c in both if (t.loc[c, "ratio_top"] < 1) != (x.loc[c, "ratio"] < 1)]
    print(f"\ncommon target: trees cheaper in {(t.ratio_top < 1).sum()} of the {len(t)} cities with a "
          f"defined tree ratio (baseline rule: {(x.ratio < 1).sum()} of {len(x)}); "
          f"{len(changed)} change side: {sorted(changed)}")
    print("  to trees:", sorted(c for c in changed if t.loc[c, "ratio_top"] < 1),
          " to cooling:", sorted(c for c in changed if t.loc[c, "ratio_top"] >= 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

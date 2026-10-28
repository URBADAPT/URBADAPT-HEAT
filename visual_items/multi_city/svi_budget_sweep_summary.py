"""Equal-budget AC and street-tree sweep allocated by social vulnerability (NB11): 40-city summary.

Reads each city's tables/svi_budget_sweep_<city>.csv and compares, at every per-capita budget:
  * AC against street trees, both allocated to the most vulnerable units first (SVI priority):
    cost per avoided death, deaths avoided per 100 000, cities where trees are cheaper;
  * SVI priority against each pathway's own rule at the same budget (income for AC, greenness
    deficit for trees): the share of the benefit reaching the two most vulnerable quintiles and
    what it costs in avoided deaths.
Tree results are reported for both ceilings (the city's greenest region, and GVI 35).

Usage:
    python svi_budget_sweep_summary.py
    OUT_TREE=<runs tree> META=<city_metadata.csv> python svi_budget_sweep_summary.py
Writes svi_budget_sweep_all.csv and svi_budget_sweep_summary.csv into OUT_TREE.
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TREE = os.environ.get("OUT_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\svi_40")
META = os.environ.get("META", os.path.join(HERE, "tables", "city_metadata.csv"))
CLASSES = ["Hot", "Temperate", "Cool"]


def load() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(OUT_TREE, "*", "tables", "svi_budget_sweep_*.csv")))
    files = [f for f in files if "_validation_" not in os.path.basename(f)]
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    meta = pd.read_csv(META)[["city", "climate_cluster"]]
    d = d.merge(meta, on="city", how="left")
    d["q45"] = d["share_Q4"] + d["share_Q5-High"]
    d["av_100k"] = d["avoided_25y_net"] / d["pop_city"] * 1e5
    return d


def pair(d: pd.DataFrame, a: str, b: str, cols: list[str]) -> pd.DataFrame:
    x = d[d.strategy == a].set_index(["city", "budget_pc_eur"])[cols]
    y = d[d.strategy == b].set_index(["city", "budget_pc_eur"])[cols]
    return x.join(y, lsuffix=f"_{a}", rsuffix=f"_{b}", how="inner").reset_index()


def main() -> int:
    d = load()
    print(f"[inputs] {OUT_TREE}: {d.city.nunique()} cities, strategies {sorted(d.strategy.unique())}")
    rows = []
    for trees in ["trees_svi", "trees_svi_abs"]:
        p = pair(d, "ac_svi", trees, ["cost_per_death_eur", "av_100k", "spent_eur", "budget_eur", "saturated"])
        for b, g in p.groupby("budget_pc_eur"):
            ok = np.isfinite(g[f"cost_per_death_eur_{trees}"])
            cheaper = int((g.loc[ok, f"cost_per_death_eur_{trees}"] < g.loc[ok, "cost_per_death_eur_ac_svi"]).sum())
            rows.append({
                "comparison": f"ac_svi vs {trees}", "budget_pc_eur": b,
                "ac_cpd_median_meur": g["cost_per_death_eur_ac_svi"].median() / 1e6,
                "trees_cpd_median_meur": g.loc[ok, f"cost_per_death_eur_{trees}"].median() / 1e6,
                "trees_cheaper_cities": cheaper, "cities_with_tree_benefit": int(ok.sum()),
                "ac_av100k_median": g["av_100k_ac_svi"].median(),
                "trees_av100k_median": g[f"av_100k_{trees}"].median(),
                "trees_saturated_cities": int(g[f"saturated_{trees}"].sum()),
                "ac_saturated_cities": int(g["saturated_ac_svi"].sum()),
            })
    for svi, base in [("ac_svi", "ac_income"), ("trees_svi", "trees_deficit"), ("trees_svi_abs", "trees_deficit_abs")]:
        p = pair(d, svi, base, ["q45", "avoided_25y_net"])
        for b, g in p.groupby("budget_pc_eur"):
            ok = g[f"avoided_25y_net_{base}"] > 0
            rows.append({
                "comparison": f"{svi} vs {base}", "budget_pc_eur": b,
                "q45_svi_median": g[f"q45_{svi}"].median(), "q45_base_median": g[f"q45_{base}"].median(),
                "q45_gain_pp_median": 100 * (g[f"q45_{svi}"] - g[f"q45_{base}"]).median(),
                "avoided_change_pct_median": 100 * (g.loc[ok, f"avoided_25y_net_{svi}"] / g.loc[ok, f"avoided_25y_net_{base}"] - 1).median(),
            })
    s = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    for comp, g in s.groupby("comparison", sort=False):
        print(f"\n== {comp}")
        print(g.drop(columns="comparison").dropna(axis=1, how="all").round(3).to_string(index=False))

    out_dir = OUT_TREE
    d.to_csv(os.path.join(out_dir, "svi_budget_sweep_all.csv"), index=False)
    s.to_csv(os.path.join(out_dir, "svi_budget_sweep_summary.csv"), index=False)
    print(f"\n[saved] svi_budget_sweep_all.csv, svi_budget_sweep_summary.csv in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

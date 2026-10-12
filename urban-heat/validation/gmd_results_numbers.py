"""Every quantity the GMD Results section quotes, from the corrected pilot run.

Reads outputs_variants/masselot_main_gmd, the dedicated tree produced by
juno_run_gmd_pilots.lsf with both corrections. Its deterministic outputs were checked
bit-identical against the 40-city both-corrections arm, so the two agree by construction.

Usage:
    python validation/gmd_results_numbers.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TREE = os.path.join(ROOT, "outputs_variants", "masselot_main_gmd")
CITIES = ("rome", "athens", "lisbon", "copenhagen")
FAM = "masselot_tail_varcorr"


def _csv(*p):
    f = os.path.join(*p)
    return pd.read_csv(f) if os.path.exists(f) else None


def row(city: str) -> dict:
    r: dict[str, float | str] = {"city": city}

    # Climate-only branch, before attenuation by existing AC: one column per age group.
    g = _csv(TREE, city, "interim", f"annual_heat_deaths_generic_{city}_{FAM}.csv")
    if g is not None:
        ages = [c for c in g.columns if c != "year"]
        tot = g.set_index("year")[ages].sum(axis=1)
        r["clim_2020"] = float(tot.get(2020, float("nan")))
        r["clim_2050"] = float(tot.get(2050, float("nan")))
        if "65+" in g.columns:
            r["clim_2050_65plus"] = float(g.set_index("year")["65+"].get(2050, float("nan")))

    # Current-AC reference branch, all ages.
    b = _csv(TREE, city, "tables", f"annual_heat_deaths_baseline_current_ac_{city}.csv")
    if b is not None:
        s = b.set_index("year")["deaths_overall"]
        r["currentac_2020"] = float(s.get(2020, float("nan")))
        r["currentac_2050"] = float(s.get(2050, float("nan")))

    c = _csv(TREE, city, "tables", f"{city}_cea_summary.csv")
    if c is not None:
        c = c.set_index("Policy")
        for key, label in (("ac_net", "AC (NET)"), ("ac_gross", "AC (GROSS)"),
                           ("trees", "Trees (base O&M)"), ("ews", "EWS (central)")):
            if label in c.index:
                r[f"{key}_deaths"] = float(c.loc[label, "Avoided_Deaths_25y"])
                r[f"{key}_cpd"] = float(c.loc[label, "Cost_per_Death_EUR"])
        if {"ac_gross_deaths", "ac_net_deaths"} <= r.keys():
            pen = r["ac_gross_deaths"] - r["ac_net_deaths"]
            r["wasteheat_penalty"] = pen
            r["wasteheat_pct_of_gross"] = 100 * pen / r["ac_gross_deaths"]
        # tab:city_results "Aggregate standalone ratio": the three standalone PV costs
        # over the three standalone benefits, AC net of waste heat, trees at base O&M.
        standalone = ["AC (NET)", "EWS (central)", "Trees (base O&M)"]
        if set(standalone) <= set(c.index):
            r["aggregate_standalone_cpd"] = float(
                c.loc[standalone, "PV_Cost_EUR"].sum() / c.loc[standalone, "Avoided_Deaths_25y"].sum()
            )
    return r


def main() -> int:
    if not os.path.isdir(TREE):
        print(f"[error] no tree at {TREE}")
        return 2
    t = pd.DataFrame([row(c) for c in CITIES]).set_index("city")
    pd.set_option("display.width", 220)

    def show(title, keys, fmt="{:,.1f}"):
        print(f"\n{title}")
        for k in keys:
            if k not in t:
                print(f"  {k:24s} (absent)")
                continue
            print(f"  {k:24s} " + "  ".join(f"{c[:4]} {fmt.format(t.loc[c, k])}" for c in CITIES))

    show("Climate-only branch (before current-AC attenuation)",
         ["clim_2020", "clim_2050", "clim_2050_65plus"])
    show("Current-AC reference branch", ["currentac_2020", "currentac_2050"])
    show("Adaptation benefits, 25 y avoided deaths",
         ["ac_gross_deaths", "ac_net_deaths", "ews_deaths", "trees_deaths"])
    show("AC waste-heat penalty", ["wasteheat_penalty", "wasteheat_pct_of_gross"])
    show("Cost per avoided death (EUR)",
         ["aggregate_standalone_cpd", "ac_net_cpd", "ews_cpd", "trees_cpd"], "{:,.0f}")

    out = os.path.join(HERE, "outputs", "gmd_results_numbers.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t.to_csv(out)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

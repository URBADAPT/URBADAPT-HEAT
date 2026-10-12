"""What changes in the GMD pilot results table once both corrections are canonical.

Rebuilds the rows of `tab:city_results` from the published uncorrected run and from the
both-corrections run, side by side, so the manuscript numbers can be updated from
evidence rather than re-derived by hand.

The two trees differ in which impact-function family is canonical - `masselot_tail` in
the published run, `masselot_tail_varcorr` in the corrected one - so the climate-only
mortality rows are read per tree from its own canonical family rather than from a fixed
filename, which would silently compare a corrected tree's uncorrected sensitivity family.

Usage:
    python validation/compare_gmd_pilot_table.py
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

CITIES = ("rome", "athens", "lisbon", "copenhagen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

OLD = os.path.join(ROOT, "outputs_variants", "masselot_main")
NEW = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40"

# Per tree, the canonical family in the order it should be preferred.
FAMILIES = {
    "published": ("masselot_tail",),
    "corrected": ("masselot_tail_varcorr", "masselot_tail"),
}


def _csv(*parts) -> pd.DataFrame | None:
    p = os.path.join(*parts)
    return pd.read_csv(p) if os.path.exists(p) else None


def climate_only(base: str, city: str, which: str) -> tuple[float, float, str]:
    """2020 and 2050 heat deaths before current-AC attenuation, and the family used."""
    for fam in FAMILIES[which]:
        d = _csv(base, city, "interim", f"annual_heat_deaths_generic_{city}_{fam}.csv")
        if d is None:
            continue
        # The file is one column per age group, not a total column: sum the age groups
        # rather than looking for a `deaths_overall` that this table never had.
        ages = [c for c in d.columns if c != "year"]
        if not ages:
            continue
        g = d.set_index("year")[ages].sum(axis=1)
        return float(g.get(2020, float("nan"))), float(g.get(2050, float("nan"))), fam
    return float("nan"), float("nan"), "-"


def cea(base: str, city: str) -> pd.DataFrame | None:
    d = _csv(base, city, "tables", f"{city}_cea_summary.csv")
    return d.set_index("Policy") if d is not None else None


def ews_25y(base: str, city: str) -> float:
    d = _csv(base, city, "tables", f"ews_benefits_25y_{city}.csv")
    if d is None:
        return float("nan")
    # One row per year and efficacy scenario; the table reports the central scenario.
    scen = "central" if "central" in set(d.scenario) else sorted(set(d.scenario))[0]
    return float(d.loc[d.scenario == scen, "net_avoided_deaths"].sum())


def main() -> int:
    rows = []
    for city in CITIES:
        rec: dict[str, object] = {"city": city}
        for which, base in (("published", OLD), ("corrected", NEW)):
            d20, d50, fam = climate_only(base, city, which)
            rec[f"deaths2020_{which}"] = d20
            rec[f"deaths2050_{which}"] = d50
            rec[f"family_{which}"] = fam
            rec[f"ews25_{which}"] = ews_25y(base, city)
            c = cea(base, city)
            if c is not None:
                for key, label in (("trees", "Trees (base O&M)"),):
                    if label in c.index:
                        rec[f"{key}25_{which}"] = float(c.loc[label, "Avoided_Deaths_25y"])
                        rec[f"{key}_cost_{which}"] = float(c.loc[label, "Cost_per_Death_EUR"])
                for label in c.index:
                    if "ir cond" in label or label.lower().startswith("ac"):
                        rec[f"ac25_{which}"] = float(c.loc[label, "Avoided_Deaths_25y"])
                        rec[f"ac_cost_{which}"] = float(c.loc[label, "Cost_per_Death_EUR"])
                        break
        rows.append(rec)

    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)

    def show(title: str, pub: str, cor: str, fmt: str = "{:,.1f}") -> None:
        print(f"\n{title}")
        for _, r in t.iterrows():
            a, b = r.get(pub), r.get(cor)
            if pd.isna(a) or pd.isna(b):
                print(f"  {r.city:12s} {'n/a':>12s}  {'n/a':>12s}")
                continue
            ratio = b / a if a else float("nan")
            print(f"  {r.city:12s} {fmt.format(a):>12s}  ->{fmt.format(b):>12s}   "
                  f"x{ratio:,.2f}")

    fams = {(r.family_published, r.family_corrected) for _, r in t.iterrows()}
    print(f"impact-function families in use (published, corrected): {sorted(fams)}")
    show("Heat deaths before current-AC attenuation, 2020",
         "deaths2020_published", "deaths2020_corrected")
    show("Heat deaths before current-AC attenuation, 2050",
         "deaths2050_published", "deaths2050_corrected")
    show("EWS net avoided deaths, 25y", "ews25_published", "ews25_corrected")
    show("Trees avoided deaths, 25y", "trees25_published", "trees25_corrected")
    show("AC avoided deaths (net), 25y", "ac25_published", "ac25_corrected")
    show("Trees cost per avoided death (EUR)",
         "trees_cost_published", "trees_cost_corrected", "{:,.0f}")
    show("AC cost per avoided death (EUR)",
         "ac_cost_published", "ac_cost_corrected", "{:,.0f}")

    out = os.path.join(HERE, "outputs", "gmd_pilot_table_comparison.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

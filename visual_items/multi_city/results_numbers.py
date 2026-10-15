"""Every number the multi-city Results section quotes, recomputed from the build.

Reads the tables the R build itself writes, so the prose cannot drift from the figures:
anything computed here a second way could disagree with what the panels show. Where a
quantity is not in those tables it is taken from the corrected output tree directly.

Usage:
    python results_numbers.py                      # corrected run
    RESULTS_DIR=<path to compare/published> python results_numbers.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

D = os.environ.get(
    "RESULTS_DIR",
    r"C:\Users\giaco\Documents\Github\URBADAPT-HEAT\visual_items\multi_city\compare\corrected")
TREE = os.environ.get(
    "OUT_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40")

ORDER = ["Hot", "Temperate", "Cool"]


def t(name: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(D, name))


def col(d: pd.DataFrame, *frag: str) -> str:
    """First column containing all fragments - the headers carry a euro sign whose
    encoding differs between platforms, so they are matched rather than quoted."""
    for c in d.columns:
        if all(f.lower() in c.lower() for f in frag):
            return c
    raise KeyError(f"no column matching {frag} in {list(d.columns)}")


def med_by_class(d: pd.DataFrame, value: str, cls: str = "Cluster") -> pd.Series:
    return d.groupby(cls)[value].median().reindex(ORDER)


def main() -> int:
    cba, risk, ews = t("tab_cba_by_city.csv"), t("tab_risk_by_city.csv"), t("tab_ews_by_city.csv")
    meta = pd.read_csv(os.path.join(D, "city_metadata.csv"))

    print("CLASS COUNTS")
    n = meta.climate_cluster.value_counts().reindex(ORDER)
    print("  " + ", ".join(f"{k.lower()} {v}" for k, v in n.items()))
    for cl in ORDER:
        s = meta[meta.climate_cluster == cl]
        print(f"    {cl:10s} warm {s.warmseason_mean_t2m.min():.1f}-{s.warmseason_mean_t2m.max():.1f} C, "
              f"hot days {s.hot_days.min():.0f}-{s.hot_days.max():.0f}")

    cpd = col(cba, "cost per death")
    pvc = col(cba, "pv cost per capita")
    pv = col(cba, "pv cost (")
    av = col(cba, "avoided deaths")

    print("\nMEDIAN COST PER AVOIDED DEATH, ALL 40 CITIES  (k EUR)")
    for p, g in cba.groupby("Pathway"):
        v = g[cpd].replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  {p:10s} median {v.median():12,.1f} k   (n={len(v)})")

    print("\nHOT vs COOL MEDIAN COST PER AVOIDED DEATH  (k EUR)")
    for p, g in cba.groupby("Pathway"):
        m = med_by_class(g.replace([np.inf, -np.inf], np.nan), cpd)
        r = m["Cool"] / m["Hot"] if m["Hot"] else np.nan
        print(f"  {p:10s} Hot {m['Hot']:12,.1f}  Temperate {m['Temperate']:12,.1f}  "
              f"Cool {m['Cool']:12,.1f}   ratio x{r:,.1f}")

    print("\nPER-CAPITA PV COST, POPULATION-WEIGHTED BY CLASS  (EUR)")
    pop = risk.set_index("City")[col(risk, "pop (k)")] * 1000
    for p, g in cba.groupby("Pathway"):
        g = g.copy()
        g["pop"] = g.City.map(pop)
        g["tot"] = g[pv] * 1e6
        agg = g.groupby("Cluster").apply(lambda x: x.tot.sum() / x["pop"].sum())
        agg = agg.reindex(ORDER)
        print(f"  {p:10s} " + "  ".join(f"{c} {agg[c]:>8,.0f}" for c in ORDER))

    print("\nCITY-LEVEL ORDERING BETWEEN PATHWAYS")
    w = cba.pivot_table(index="City", columns="Pathway", values=cpd)
    paths = list(w.columns)
    tr = next((c for c in paths if "tree" in c.lower()), None)
    ac = next((c for c in paths if c.lower().startswith("ac") or "cond" in c.lower()), None)
    if tr and ac:
        ok = w[[tr, ac]].replace([np.inf, -np.inf], np.nan).dropna()
        cheaper = ok.index[ok[tr] < ok[ac]]
        print(f"  trees cheaper than AC in {len(cheaper)} of {len(ok)} cities")
        byc = meta.set_index("city_name" if "city_name" in meta else "city").climate_cluster
        for cl in ORDER:
            members = [c for c in cheaper if byc.get(c) == cl]
            print(f"    {cl:10s} {', '.join(sorted(members)) if members else '-'}")

    wpc = cba.pivot_table(index="City", columns="Pathway", values=pvc)
    if tr and ac and tr in wpc and ac in wpc:
        ok = wpc[[tr, ac]].dropna()
        print(f"  per-capita tree spend exceeds AC in {(ok[tr] > ok[ac]).sum()} of {len(ok)} cities")

    print("\nEWS ATTRIBUTION BY CLASS")
    if "Attribution" in ews:
        ct = pd.crosstab(ews.Cluster, ews.Attribution).reindex(ORDER)
        print(ct.to_string())

    print("\nBASELINE BURDEN")
    per = col(risk, "100k")
    v = risk[per].replace(0, np.nan).dropna()
    print(f"  per 100k: {v.min():.2f} ({risk.loc[risk[per] == v.min(), 'City'].iloc[0]}) to "
          f"{v.max():.2f} ({risk.loc[risk[per] == v.max(), 'City'].iloc[0]})")
    print("  median by class: " + ", ".join(
        f"{c} {med_by_class(risk, per)[c]:.1f}" for c in ORDER))
    return 0


if __name__ == "__main__":
    sys.exit(main())

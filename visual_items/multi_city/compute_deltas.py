"""Recompute the headline rows of the published-vs-corrected comparison from data.

make_comparison.py used to carry these as literals. Several are functions of the
climate class, which moved when the class was re-based on observed ERA5-Land, so a
literal written before that is quietly wrong - fig1a was recorded at x1.23 and the
rebuild emits x1.26. Anything derivable is derived here instead.

Each column is scored on ITS OWN climate class: the published figures were drawn with
the model-hazard class, so scoring them against the new one would attribute part of the
class change to the corrections.

Usage:
    python compute_deltas.py
"""

from __future__ import annotations

import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(REPO), "urban-heat", "validation"))
from modelled_population import modelled_population  # noqa: E402

PUB_OUT = r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"
COR_OUT = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40"
PUB_META = r"C:\Users\giaco\Documents\Github\URBADAPT-HEAT\visual_items\multi_city\tables\city_metadata.csv"
COR_META = os.path.join(HERE, "tables", "city_metadata.csv")

# Pathway rows are matched by pattern, never by exact label: the EWS row carries the
# city's attribution wording (counterfactual / marginal / intermediate) and the tree row
# its O&M variant, so an equality test drops cities silently.
NO_BENEFIT_DEATHS = 0.5   # matches _helpers.R

PATHWAY = {
    "AC": re.compile(r"^AC\s*\(NET\)", re.I),
    "Trees": re.compile(r"^Trees\s*\(base", re.I),
    "EWS": re.compile(r"^EWS", re.I),
}


def city_rows(base: str, meta_path: str) -> pd.DataFrame:
    meta = pd.read_csv(meta_path)[["city", "climate_cluster"]]
    out = []
    for _, m in meta.iterrows():
        c = m.city
        cea = os.path.join(base, c, "tables", f"{c}_cea_summary.csv")
        dea = os.path.join(base, c, "tables", f"annual_heat_deaths_baseline_current_ac_{c}.csv")
        if not all(os.path.exists(p) for p in (cea, dea)):
            continue
        d = pd.read_csv(cea)
        rec = {"city": c, "cluster": m.climate_cluster}
        for name, pat in PATHWAY.items():
            hit = d[d.Policy.astype(str).str.match(pat)]
            if not len(hit):
                rec[name] = np.nan
                continue
            cost = float(hit.Cost_per_Death_EUR.iloc[0])
            avoided = float(hit.Avoided_Deaths_25y.iloc[0])
            # Same censoring as _helpers.R: a pathway avoiding ~0 deaths reports a
            # 1e10 EUR artefact rather than a cost. Madrid and Sevilla greening hit
            # this, and both are Hot, so leaving them in drags the Hot median up and
            # understates the gradient - which is how the trees row came out at x6.7
            # against the x12 the figures show.
            rec[name] = np.nan if not np.isfinite(cost) or avoided < NO_BENEFIT_DEATHS else cost
        # The population of the modelled area does not depend on the corrections, so
        # fall back to the corrected tree where an older export lacks the EWS tables.
        pop = modelled_population(os.path.join(base, c, "tables"), c)
        if not np.isfinite(pop):
            pop = modelled_population(os.path.join(COR_OUT, c, "tables"), c)
        dd = pd.read_csv(dea)
        v = dd.loc[dd.year == 2020, "deaths_overall"]
        rec["per100k"] = float(v.iloc[0]) / pop * 1e5 if len(v) and np.isfinite(pop) else np.nan
        out.append(rec)
    return pd.DataFrame(out)


def gradient(t: pd.DataFrame, col: str) -> tuple[float, float, float]:
    """Median cost per avoided death in Hot vs Cool, and their ratio."""
    med = t.groupby("cluster")[col].median()
    hot, cool = med.get("Hot", np.nan), med.get("Cool", np.nan)
    return hot, cool, (cool / hot if hot else np.nan)


def main() -> int:
    pub, cor = city_rows(PUB_OUT, PUB_META), city_rows(COR_OUT, COR_META)
    print(f"cities: published {len(pub)}, corrected {len(cor)}\n")
    for tag, t in (("published", pub), ("corrected", cor)):
        print(f"{tag}: cluster sizes {t.cluster.value_counts().to_dict()}")
    print()

    for name in ("AC", "Trees", "EWS"):
        print(f"{name} cost per avoided death, Hot -> Cool")
        for tag, t in (("published", pub), ("corrected", cor)):
            h, c, r = gradient(t, name)
            print(f"  {tag:10s} Hot EUR {h:14,.0f}   Cool EUR {c:14,.0f}   "
                  f"ratio x{r:,.1f}")
        print()

    print("Baseline burden per 100k, 2020")
    for tag, t in (("published", pub), ("corrected", cor)):
        v = t.per100k.replace(0, np.nan).dropna()
        lo, hi = v.min(), v.max()
        lo_c = t.loc[t.per100k == lo, "city"].iloc[0] if (t.per100k == lo).any() else "?"
        hi_c = t.loc[t.per100k == hi, "city"].iloc[0] if (t.per100k == hi).any() else "?"
        print(f"  {tag:10s} {lo:8.2f} ({lo_c}) to {hi:8.2f} ({hi_c})   "
              f"{np.log10(hi / lo):.1f} orders of magnitude   n={len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

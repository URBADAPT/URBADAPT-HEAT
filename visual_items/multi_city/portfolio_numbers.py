"""The budget-sweep deployment numbers the multi-city paper quotes, from a 40-city output tree.

fig1_risk_costs_portfolios.R prints the frontier numbers of panel c itself; these are the
panel d quantities of the Results paragraph on portfolio sequencing, which were otherwise
transcribed by hand:

  budget per capita covered by the sweep, and whether warning systems are always fully deployed
  at each city's lowest budget, in how many cities cooling is deployed more than street trees
  median deployment share of each pathway by band of budget per capita

The shares come from NB08's <city>_budget_sensitivity.csv (exact solver since commit abde2ba).

Usage:
    python portfolio_numbers.py <tree>          # e.g. ...\\juno\\ewsfix40
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BANDS = [0, 500, 750, 1000, 1500, 2000, np.inf]


def main() -> int:
    tree = Path(sys.argv[1])
    meta = pd.read_csv(HERE / "tables" / "city_metadata.csv").set_index("city")
    rows = []
    for city in meta.index:
        t = pd.read_csv(tree / city / "tables" / f"{city}_budget_sensitivity.csv")
        t["city"] = city
        t["budget_cap"] = t["budget"] / (meta.loc[city, "pop_k"] * 1000)
        rows.append(t)
    b = pd.concat(rows, ignore_index=True)

    print(f"cities: {b.city.nunique()};  budget per capita EUR {b.budget_cap.min():.0f}-{b.budget_cap.max():.0f}")
    print(f"warning systems fully deployed at every city-budget point: {bool((b.max_ben_ews >= 1 - 1e-9).all())}")
    low = b.loc[b.groupby("city")["budget"].idxmin()]
    tie = np.isclose(low.max_ben_ac, low.max_ben_trees)
    print(f"at each city's lowest budget, cooling deployed more than trees in "
          f"{int(((low.max_ben_ac > low.max_ben_trees) & ~tie).sum())} of {len(low)} cities "
          f"(trees more in {int(((low.max_ben_trees > low.max_ben_ac) & ~tie).sum())}, tied in {int(tie.sum())})")

    b["band"] = pd.cut(b.budget_cap, BANDS, right=False)
    g = b.groupby("band", observed=True).agg(points=("city", "size"), cooling=("max_ben_ac", "median"),
                                            trees=("max_ben_trees", "median"), warning=("max_ben_ews", "median"))
    print("\nmedian share of full deployment, by budget per capita (EUR):")
    print(g.round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())

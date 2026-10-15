"""Population of the modelled area of a city, read back from its run outputs.

Deaths, and the warning-system costs, are summed over every populated cell of the
modelled area (CITY_MASK: the part of the FUA inside the UrbClim domain). Per-capita
and per-100k figures must divide by that population. The per-region `population` in
<city>_policy_comparison.csv is not it: that table covers only the greening regions,
which can be a small part of the area (Athens: 0.67M of 3.42M).

No table stores the area total directly, but NB06 charges the variable warning cost to
it: cost_opex_variable = population * n_warning_days * usd_per_capita_per_day *
eur_usd_rate. Inverting that recovers the gridded total exactly (checked against
pop_on_ref_<city>_2020.npz for seven cities).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def modelled_population(tables_dir: str | Path, slug: str, year: int = 2020) -> float:
    """Population of the modelled area in `year` (an NB06 anchor year), or NaN if unavailable."""
    tables_dir = Path(tables_dir)
    costs_csv = tables_dir / f"{slug}_ews_costs_annual.csv"
    params_json = tables_dir / f"{slug}_ews_parameters.json"
    if not (costs_csv.exists() and params_json.exists()):
        return float("nan")
    costs = json.loads(params_json.read_text(encoding="utf-8")).get("costs", {})
    if costs.get("cost_model") != "pavanello":
        return float("nan")
    unit = float(costs["pavanello"]["usd_per_capita_per_day"]) * float(costs["pavanello"]["eur_usd_rate"])
    row = pd.read_csv(costs_csv).loc[lambda d: d.year == year]
    if row.empty:
        return float("nan")
    days = float(row.n_warning_days.iloc[0])
    if days <= 0 or unit <= 0:
        return float("nan")
    return float(row.cost_opex_variable.iloc[0]) / (days * unit)

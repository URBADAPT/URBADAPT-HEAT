"""The warning-pathway numbers the Nature Cities text quotes, from a 40-city output tree.

results_numbers.py covers cost-effectiveness; these are the warning-day and cost-structure
quantities of the Results paragraph on the temporal EWS interaction and of the Methods EWS
cost description, which were otherwise transcribed by hand:

  warning days per season, 2020 and 2050, median by climate class
  share of annual heat deaths falling on warning days, 2020 and 2050, median of 40 cities
  population-proportional share of annual EWS operating cost, 2020 and 2050, median
  setup CAPEX as a share of 25-year PV cost, median and maximum

Usage:
    python ews_numbers.py <tree>          # e.g. ...\\juno\\ewsfix40
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ORDER = ["Hot", "Temperate", "Cool"]


def main() -> int:
    tree = Path(sys.argv[1])
    meta = pd.read_csv(HERE / "tables" / "city_metadata.csv").set_index("city")
    rows = []
    for city in meta.index:
        t = tree / city / "tables"
        w = pd.read_csv(t / f"{city}_ews_warning_days.csv").set_index("year")
        b = pd.read_csv(t / f"annual_heat_deaths_baseline_current_ac_{city}.csv").set_index("year")
        c = pd.read_csv(t / f"{city}_ews_costs_annual.csv").set_index("year")
        ts = pd.read_csv(t / f"ews_timeseries_25y_{city}.csv")
        opex = c.cost_opex_variable + c.cost_opex_fixed
        rows.append({
            "city": city, "cls": meta.loc[city, "climate_cluster"],
            "wd_2020": w.loc[2020, "n_warning_days"], "wd_2050": w.loc[2050, "n_warning_days"],
            "share_2020": 100 * b.loc[2020, "frac_deaths_on_warning_days"],
            "share_2050": 100 * b.loc[2050, "frac_deaths_on_warning_days"],
            "var_share_2020": 100 * c.loc[2020, "cost_opex_variable"] / opex.loc[2020],
            "var_share_2050": 100 * c.loc[2050, "cost_opex_variable"] / opex.loc[2050],
            "capex_share_pv": 100 * ts.cost_capex.sum() / ts.cost_pv.sum(),
        })
    d = pd.DataFrame(rows)
    print("WARNING DAYS PER SEASON, MEDIAN BY CLASS")
    for col in ("wd_2020", "wd_2050"):
        print(f"  {col}  " + "  ".join(f"{k} {d.loc[d.cls == k, col].median():.0f}" for k in ORDER)
              + f"  (all {d[col].median():.0f})")
    print("SHARE OF ANNUAL HEAT DEATHS ON WARNING DAYS, MEDIAN OF 40 CITIES (%)")
    print(f"  2020 {d.share_2020.median():.1f}   2050 {d.share_2050.median():.1f}"
          f"   by class 2020: " + "  ".join(f"{k} {d.loc[d.cls == k, 'share_2020'].median():.1f}" for k in ORDER))
    print("EWS COST STRUCTURE (%)")
    print(f"  population-proportional share of operating cost: 2020 median {d.var_share_2020.median():.0f}, "
          f"2050 median {d.var_share_2050.median():.0f}")
    print(f"  setup CAPEX share of 25y PV cost: median {d.capex_share_pv.median():.1f}, max {d.capex_share_pv.max():.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

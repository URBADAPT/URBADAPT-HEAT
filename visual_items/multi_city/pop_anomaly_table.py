"""Population-weighted temperature anomaly about the city reference, per city, with the
first-order effect on baseline heat deaths of re-centring cell anomalies on it.

Inputs: one or more CSVs from urban-heat/validation/population_weighted_anomaly.py and the
outputs tree (interim/marginal_heat_deaths_perC_currentAC_<city>.csv, 2020 row). The
first-order change is -a_pop(May-Sep) x marginal deaths per degree; the response is convex,
so it overstates the decrease, most for the largest anomalies, and is an upper bound on it.
Writes <TAB_DIR>/tab_pop_anomaly.{csv,tex} and prints [caption] lines.

Usage: python pop_anomaly_table.py <outputs tree> <TAB_DIR> <anomaly.csv> [<anomaly.csv> ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

META = Path(__file__).parent / "tables" / "city_metadata.csv"
CLASSES = ["Hot", "Temperate", "Cool"]


def main() -> None:
    tree, tab_dir = Path(sys.argv[1]), Path(sys.argv[2])
    a = pd.concat([pd.read_csv(f) for f in sys.argv[3:]]).drop_duplicates("city", keep="last").set_index("city")
    rows = []
    for c in a.index:
        m = pd.read_csv(tree / c / "interim" / f"marginal_heat_deaths_perC_currentAC_{c}.csv").set_index("year")
        rows.append(dict(city=c, deaths=m.loc[2020, "deaths_T"], marginal=m.loc[2020, "marginal_per_C"]))
    d = a.join(pd.DataFrame(rows).set_index("city"))
    d["dd"] = -d["a_pop_warm_C"] * d["marginal"]
    d["dd_pct"] = 100 * d["dd"] / d["deaths"]
    meta = pd.read_csv(META).set_index("city") if META.exists() else None
    d["cluster"] = meta["climate_cluster"].reindex(d.index) if meta is not None else ""
    d["label"] = [c.replace("_", " ").title() for c in d.index]
    d["rank"] = d["cluster"].map({k: i for i, k in enumerate(CLASSES)})
    d = d.sort_values(["rank", "label"])

    out = pd.DataFrame({
        "City": d["label"], "Cluster": d["cluster"],
        "Anomaly, year (C)": d["a_pop_year_C"].map(lambda v: f"{v:+.2f}"),
        "Anomaly, May-Sep (C)": d["a_pop_warm_C"].map(lambda v: f"{v:+.2f}"),
        "Heat deaths 2020": d["deaths"].round(0).astype(int),
        "Deaths per C": d["marginal"].round(0).astype(int),
        "First-order change (%)": d["dd_pct"].map(lambda v: f"{v:+.0f}"),
    })
    tab_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(tab_dir / "tab_pop_anomaly.csv", index=False)
    head = ("City & Cluster & \\multicolumn{2}{c}{Anomaly (\\textdegree C)} & Heat deaths & Deaths & First-order \\\\\n"
            " & & Year & May-Sep & 2020 & per \\textdegree C & change (\\%) \\\\\n")
    body = "\n".join(" & ".join(map(str, r)) + " \\\\" for r in out.itertuples(index=False))
    tex = ("\\begingroup\\scriptsize\n\\begin{longtable}{llrrrrr}\n"
           "\\caption{Population-weighted temperature anomaly of residents about the city reference to which the "
           "exposure-response functions are matched (the unweighted mean over the full UrbClim domain), for the "
           "2008-2017 climatology, over the year and over May to September. The last column is the first-order "
           "change in 2020 baseline heat-attributable deaths if cell anomalies were re-centred on the "
           "population-weighted mean (minus the May-September anomaly times the marginal deaths per degree); "
           "because the response is convex it overstates the decrease, most for the largest anomalies.}\n"
           "\\label{tab:pop_anomaly}\\\\\n\\toprule\n" + head + "\\midrule\n\\endfirsthead\n\\toprule\n" + head +
           "\\midrule\n\\endhead\n" + body + "\n\\bottomrule\n\\end{longtable}\n\\endgroup\n")
    (tab_dir / "tab_pop_anomaly.tex").write_text(tex, encoding="utf-8")
    print(f"[saved] {tab_dir / 'tab_pop_anomaly.tex'} ({len(out)} cities)")

    w = d["a_pop_warm_C"]
    print(f"[caption] May-Sep anomaly: median {w.median():+.2f} C, range {w.min():+.2f} to {w.max():+.2f}; "
          f"positive in {int((w > 0).sum())} of {len(w)}; >= +0.8 C in {', '.join(d.label[w >= 0.8])}")
    print(f"[caption] first-order change in 2020 baseline deaths: median {d.dd_pct.median():+.0f}%, "
          f"range {d.dd_pct.min():+.0f}% to {d.dd_pct.max():+.0f}%; pooled {100 * d.dd.sum() / d.deaths.sum():+.0f}%")
    for k in CLASSES:
        s = d[d["cluster"] == k]
        if len(s):
            print(f"[caption] {k} (n={len(s)}): median anomaly {s.a_pop_warm_C.median():+.2f} C, "
                  f"median first-order change {s.dd_pct.median():+.0f}%")


if __name__ == "__main__":
    main()

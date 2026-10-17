"""Supplementary Table tab:uncertainty_by_city, from the 40 per-city NB09 campaigns.

Reads each city's headline N=128 campaign (unc_samples + unc_cba_{ac,trees,ews}) and
writes tables/tab_uncertainty_by_city.{csv,tex}, plus the cross-city ensemble numbers
the Results text quotes. Columns follow the table caption:

  annual deaths            P50 [P5-P95] of annual_deaths over all members
  AC / tree / EWS cost     P50 [P5-P95] of 25-year PV cost per undiscounted death avoided,
                           over members with positive avoided deaths (AC net of waste heat)
  AC waste heat            penalty as % of gross AC benefit, over members with the
                           mechanism enabled and positive gross benefit
  P(AC > 0)                share of members with positive cumulative net AC avoided deaths
  P(trees < AC)            share of members in which trees cost less per death than AC,
                           among members in which both pathways avoid deaths

The share of members in which EWS is cheapest is near-invariant (the Results text says so
and quotes it), so it is reported in the caption rather than tabulated. --legacy-columns
restores the September layout, P(EWS cheapest) and an unconditional P(trees < AC) with a
pathway that avoids no deaths counted as infinitely expensive; it reproduces the table
published from campaign n128_seed42_v3_616fbc2d5131 cell for cell.

Usage:
    python build_uncertainty_table.py --tree <variant dir> --campaign '*n128_seed42_v3_<commit>*'
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ORDER = ["Hot", "Temperate", "Cool"]


def long_path(p: Path) -> Path:
    """Campaign members nest deep enough to pass Windows' 260-character MAX_PATH."""
    p = p.resolve()
    if os.name == "nt" and not str(p).startswith("\\\\?\\"):
        p = Path("\\\\?\\" + str(p))
    return p


def campaign_dir(tree: Path, city: str, pattern: str) -> Path:
    runs = tree / city / "tables" / "uncertainty_runs"
    # Only completed campaigns count, so an aborted attempt beside the real run is ignored.
    hits = sorted(p for p in runs.iterdir()
                  if p.is_dir() and fnmatch.fnmatch(p.name, pattern) and (p / "CAMPAIGN_COMPLETE.json").is_file()) \
        if runs.is_dir() else []
    if len(hits) != 1:
        raise SystemExit(f"{city}: expected one completed campaign matching {pattern!r} in {runs}, found {[h.name for h in hits]}")
    return hits[0]


def load(city_dir: Path, city: str) -> pd.DataFrame:
    f = lambda name: pd.read_csv(city_dir / f"{name}_{city}_improved_fast.csv")
    s = f("unc_samples")[["sample_idx", "annual_deaths", "wh_enabled"]]
    ac = f("unc_cba_ac")[["sample_idx", "ac_pv_cost_25y", "ac_gross_avoided_deaths_25y_cum",
                          "ac_net_avoided_deaths_25y_cum", "ac_waste_heat_penalty_25y_cum"]]
    tr = f("unc_cba_trees")[["sample_idx", "tree_pv_cost_25y", "tree_avoided_deaths_25y_cum"]]
    ew = f("unc_cba_ews")[["sample_idx", "ews_pv_cost_25y", "ews_net_avoided_deaths_25y_cum"]]
    d = s.merge(ac, on="sample_idx").merge(tr, on="sample_idx").merge(ew, on="sample_idx")
    if len(d) != len(s):
        raise SystemExit(f"{city}: sample tables do not align")
    return d


def cpd(cost: pd.Series, deaths: pd.Series) -> np.ndarray:
    """Cost per avoided death; infinite where the pathway avoids no deaths."""
    c, n = cost.to_numpy(float), deaths.to_numpy(float)
    return np.where(n > 0, c / np.where(n > 0, n, 1.0), np.inf)


def q(x: np.ndarray) -> tuple[float, float, float]:
    x = x[np.isfinite(x)]
    return tuple(np.percentile(x, [50, 5, 95])) if x.size else (np.nan, np.nan, np.nan)


def city_row(d: pd.DataFrame) -> dict:
    ac = cpd(d.ac_pv_cost_25y, d.ac_net_avoided_deaths_25y_cum)
    tr = cpd(d.tree_pv_cost_25y, d.tree_avoided_deaths_25y_cum)
    ew = cpd(d.ews_pv_cost_25y, d.ews_net_avoided_deaths_25y_cum)
    wh_ok = d.wh_enabled.astype(bool).to_numpy() & (d.ac_gross_avoided_deaths_25y_cum.to_numpy(float) > 0)
    wh = (100 * d.ac_waste_heat_penalty_25y_cum / d.ac_gross_avoided_deaths_25y_cum).to_numpy(float)[wh_ok]
    both = np.isfinite(ac) & np.isfinite(tr)
    r = {"n": len(d)}
    for key, arr in (("deaths", d.annual_deaths.to_numpy(float)), ("ac", ac / 1e6), ("tree", tr / 1e6),
                     ("ews", ew / 1e3), ("wh", wh)):
        r[f"{key}_p50"], r[f"{key}_p5"], r[f"{key}_p95"] = q(arr)
    r["ews_cheapest_n"] = int(np.sum((ew < ac) & (ew < tr)))
    r["p_ews_cheapest"] = r["ews_cheapest_n"] / len(d)
    r["p_trees_lt_ac"] = float(np.mean(tr < ac))
    r["p_ac_net_positive"] = float(np.mean(d.ac_net_avoided_deaths_25y_cum.to_numpy(float) > 0))
    # The ensemble samples the waste-heat feedback on or off; with it off, net equals gross.
    on = d.wh_enabled.astype(bool).to_numpy()
    r["n_wh_on"] = int(on.sum())
    r["p_ac_net_positive_wh_on"] = (float(np.mean(d.ac_net_avoided_deaths_25y_cum.to_numpy(float)[on] > 0))
                                    if on.any() else np.nan)
    r["n_both_positive"] = int(both.sum())
    r["p_trees_lt_ac_given_both"] = float(np.mean(tr[both] < ac[both])) if both.any() else np.nan
    return r


def fmt(p50: float, p5: float, p95: float, dec: int) -> str:
    if not np.isfinite(p50):
        return r"\textit{n.a.}"
    f = lambda v: f"{v:,.{dec}f}"
    return f"{f(p50)} [{f(p5)}--{f(p95)}]"


def pct(p: float) -> str:
    return f"{100 * p:.1f}\\%" if np.isfinite(p) else r"\textit{n.a.}"


CAPTION_BODY = r"""\caption{\textbf{City-level uncertainty and decision robustness across the 40-city sample.}
Entries are medians with 5th--95th percentile ranges from the $N=128$ Latin Hypercube ensemble.
The ranges summarize jointly varied plausible model configurations and are not confidence intervals
around the deterministic central configuration; the latter was evaluated separately and reproduced
the corresponding NB01--08 outputs. Annual heat-attributable mortality refers to the sampled target
year and scenario and is not an uncertainty interval around the deterministic 2020 baseline.
Cost-effectiveness is present-value cost per cumulative undiscounted death avoided over 25 years;
air-conditioning (AC) benefits are net of waste heat. Cost-effectiveness ranges use ensemble members
with positive avoided deaths. The waste-heat percentage is conditional on samples in which the
mechanism is enabled and gross AC benefits are positive. """

CAPTION_LEGACY = r"""Pairwise comparison probabilities use all 128 matched
members, treating a policy with no positive net benefit as having infinite cost per death.
"""

CAPTION_TAIL = r"""\textit{n.a.} denotes no positive modelled avoided deaths in any member; very large ratios can reflect
a near-zero health-benefit denominator rather than unusually high unit costs.}"""


def probability_caption(t: pd.DataFrame) -> str:
    exc = t.loc[t.ews_cheapest_n < t.n]
    if exc.empty:
        ews = ("Warning systems have the lowest cost per avoided death in every member of every "
               "city's ensemble, so that share is not tabulated.")
    else:
        parts = ", ".join(f"{r.city_name} ({r.ews_cheapest_n}/{r.n})" for r in exc.itertuples(index=False))
        ews = ("Warning systems have the lowest cost per avoided death in every member of every "
               f"city's ensemble except {parts}, so that share is not tabulated.")
    n_on = sorted(set(t.n_wh_on))
    on = f"the {n_on[0]}" if len(n_on) == 1 else f"the {n_on[0]} to {n_on[-1]}"
    off = f"the {int(t.n.iloc[0]) - n_on[0]}" if len(n_on) == 1 and t.n.nunique() == 1 else "those"
    lo = t.loc[t.p_ac_net_positive_wh_on.idxmin()]
    wh = (f"including {off} in which the waste-heat feedback is switched off and net equals gross; among\n"
          f"{on} members with it switched on, the share ranges from {100 * lo.p_ac_net_positive_wh_on:.0f}\\,\\%\n"
          f"({lo.city_name}) to {100 * t.p_ac_net_positive_wh_on.max():.0f}\\,\\% across cities")
    return ("$P$(AC $>0$) is the share of all 128 members with positive cumulative net AC avoided\n"
            f"deaths, {wh}. $P$(trees $<$ AC) is the share of members in which trees cost less per avoided death\n"
            "than AC, among members in which both pathways avoid deaths; \\textit{n.a.} where none do.\n"
            f"{ews}\n")


HEAD = r"""{\scriptsize
\setlength{\tabcolsep}{3pt}
\renewcommand{\arraystretch}{1.12}
\begin{longtable}{@{}>{\raggedright\arraybackslash}p{1.65cm}
                    >{\centering\arraybackslash}p{1.45cm}
                    >{\raggedleft\arraybackslash}p{2.35cm}
                    >{\raggedleft\arraybackslash}p{2.30cm}
                    >{\raggedleft\arraybackslash}p{2.45cm}
                    >{\raggedleft\arraybackslash}p{2.35cm}
                    >{\raggedleft\arraybackslash}p{2.60cm}
                    >{\raggedleft\arraybackslash}p{1.90cm}
                    >{\raggedleft\arraybackslash}p{1.90cm}@{}}
"""

COLHEAD = r"""\toprule
\textbf{City} &
\textbf{Climate class} &
\shortstack{\textbf{Annual deaths}\\\textbf{P50 [P5--P95]}} &
\shortstack{\textbf{AC cost}\\\textbf{(\texteuro{}m/death)}\\\textbf{P50 [P5--P95]}} &
\shortstack{\textbf{Tree cost}\\\textbf{(\texteuro{}m/death)}\\\textbf{P50 [P5--P95]}} &
\shortstack{\textbf{EWS cost}\\\textbf{(\texteuro{}000/death)}\\\textbf{P50 [P5--P95]}} &
\shortstack{\textbf{AC waste heat}\\\textbf{(\% gross benefit)}\\\textbf{P50 [P5--P95]}} &
"""

PROB_HEAD = r"""\shortstack{\textbf{$P$(AC}\\\textbf{$>0$)}} &
\shortstack{\textbf{$P$(trees}\\\textbf{$<$ AC)}} \\
\midrule
"""

PROB_HEAD_LEGACY = r"""\shortstack{\textbf{$P$(EWS}\\\textbf{cheapest)}} &
\shortstack{\textbf{$P$(trees}\\\textbf{$<$ AC)}} \\
\midrule
"""


def write_tex(t: pd.DataFrame, path: Path, legacy: bool) -> None:
    caption = CAPTION_BODY + (CAPTION_LEGACY if legacy else probability_caption(t)) + CAPTION_TAIL
    colhead = COLHEAD + (PROB_HEAD_LEGACY if legacy else PROB_HEAD)
    lines = [HEAD, caption + "\n", r"\label{tab:uncertainty_by_city}\\" + "\n", colhead,
             r"\endfirsthead" + "\n", r"\multicolumn{9}{c}{\tablename\ \thetable\ -- continued}\\" + "\n",
             colhead, r"\endhead" + "\n", r"\midrule" + "\n",
             r"\multicolumn{9}{r}{\textit{Continued on next page}}\\" + "\n", r"\endfoot" + "\n",
             r"\bottomrule" + "\n", r"\endlastfoot" + "\n"]
    for r in t.itertuples(index=False):
        probs = ([pct(r.p_ews_cheapest), pct(r.p_trees_lt_ac)] if legacy
                 else [pct(r.p_ac_net_positive), pct(r.p_trees_lt_ac_given_both)])
        cells = [r.city_name, r.climate_class,
                 fmt(r.deaths_p50, r.deaths_p5, r.deaths_p95, 1),
                 fmt(r.ac_p50, r.ac_p5, r.ac_p95, 1),
                 fmt(r.tree_p50, r.tree_p5, r.tree_p95, 1),
                 fmt(r.ews_p50, r.ews_p5, r.ews_p95, 0),
                 fmt(r.wh_p50, r.wh_p5, r.wh_p95, 1),
                 *probs]
        lines.append(" & ".join(cells) + r" \\" + "\n")
    lines += [r"\end{longtable}" + "\n", "}\n"]
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path, help="variant dir holding <city>/tables/uncertainty_runs")
    ap.add_argument("--campaign", required=True, help="campaign directory name or glob, one per city")
    ap.add_argument("--meta", type=Path, default=HERE / "tables" / "city_metadata.csv")
    ap.add_argument("--class-col", default="climate_cluster")
    ap.add_argument("--out-dir", type=Path, default=HERE / "tables")
    ap.add_argument("--legacy-columns", action="store_true",
                    help="September layout: P(EWS cheapest) and unconditional P(trees < AC)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="preview only: skip cities without a completed campaign instead of failing")
    args = ap.parse_args()

    meta = pd.read_csv(args.meta).set_index("city")
    tree = long_path(args.tree)
    rows = []
    for city in meta.index:
        try:
            run = campaign_dir(tree, city, args.campaign)
        except SystemExit as exc:
            if not args.allow_missing:
                raise
            print(f"  [preview] skipping {exc}")
            continue
        d = load(run, city)
        rows.append({"city": city, "city_name": meta.loc[city, "city_name"],
                     "climate_class": meta.loc[city, args.class_col], **city_row(d)})
    t = pd.DataFrame(rows)
    t["_cls"] = t.climate_class.map({c: i for i, c in enumerate(ORDER)})
    t = t.sort_values(["_cls", "deaths_p50"], ascending=[True, False]).drop(columns="_cls")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    t.to_csv(args.out_dir / "tab_uncertainty_by_city.csv", index=False)
    write_tex(t, args.out_dir / "tab_uncertainty_by_city.tex", args.legacy_columns)

    exc = t.loc[t.ews_cheapest_n < t.n]
    print(f"cities: {len(t)}  classes: {t.climate_class.value_counts().to_dict()}")
    print(f"EWS cheapest in every member: {len(t) - len(exc)}/{len(t)} cities")
    for r in exc.itertuples(index=False):
        print(f"  exception: {r.city_name} {r.ews_cheapest_n}/{r.n}")
    print(f"wrote {args.out_dir / 'tab_uncertainty_by_city.tex'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Drivers of cross-city differences in baseline heat mortality and in cost per avoided death.

Writes the SI drivers table (`tab:drivers`) and prints the numbers the text quotes from it.

  a  Shapley (LMG) decomposition of the R^2 of log baseline (2020) heat-attributable mortality
     per 100 000 on seven city descriptors: warm-season mean temperature and the residents'
     heat-island anomaly (hazard), the over-65 population share and all-cause death rate
     (demography), the over-65 heat relative risk and minimum-mortality temperature
     (exposure-response), and baseline AC coverage (existing adaptation). A descriptor's share
     is its average marginal contribution to R^2 over all orderings. Groups are scored the same
     way with each group as one player; a group's unique contribution is the loss in R^2 when
     it is dropped.
  b  Exact decomposition of the cross-city variance of log cost per avoided death through
     log(cost/death) = log(cost per capita) - log(baseline burden per capita) - log(share avoided):
     each term's share is its covariance with the total over the total variance, so the shares
     sum to one and a negative share offsets the others.
  c  Hot-versus-cool medians by pathway and, for street trees, the physical chain behind the
     gap (spend, cooling delivered, deaths averted per degree), from each run's vegetation
     diagnostics. Printed only.

Inputs
  TAB_DIR       tables written by build_all.R and pop_anomaly_table.py: city_metadata.csv,
                city_outcome_profiles.csv, tab_pop_anomaly.csv, tab_existing_ac_by_city.csv
  MASSELOT_DIR  the source study's city-age data (results/cityage.csv, metadata.csv)
  OUT_TREE      run outputs holding <city>/<city>_veg_diagnostics.json (part c; skipped if absent)

Outputs (in TAB_DIR): tab_drivers.tex, tab_drivers.csv (panel values), tab_drivers_city_data.csv

Usage:
    python drivers_decomposition.py
    TAB_DIR=compare/v3c/tables OUT_TREE=<runs> python drivers_decomposition.py
"""

from __future__ import annotations

import itertools
import json
import math
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TAB_DIR = os.environ.get("TAB_DIR", os.path.join(HERE, "tables"))
MASSELOT_DIR = os.environ.get("MASSELOT_DIR", os.path.join(REPO, "urban-heat", "10288665"))
OUT_TREE = os.environ.get("OUT_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\v3c_40")
OLD_BANDS = ["65-74", "75-84", "85+"]
CLASSES = ["Hot", "Temperate", "Cool"]

# Panel a: descriptors in table order, grouped.
GROUPS = {
    "Hazard": [("warmseason_mean_t2m", "Warm-season mean temperature"),
               ("anom", "Heat-island anomaly of residents")],
    "Demography": [("share65", "Over-65 share of population"),
                   ("dr65", "Over-65 all-cause death rate")],
    "Exposure-response": [("rrheat65", "Over-65 heat relative risk"),
                          ("mmt65", "Over-65 MMT")],
    "Existing adaptation": [("ac0", "Baseline AC coverage")],
}
# Panel b: pathway key in city_outcome_profiles.csv -> row label.
PATHWAYS = {"AC": "Air conditioning (net, {n} cities)",
            "Trees": "Street trees ({n} cities)",
            "EWS": "Warning systems ({n} cities)"}


def city_data() -> pd.DataFrame:
    meta = pd.read_csv(os.path.join(TAB_DIR, "city_metadata.csv"))
    prof = pd.read_csv(os.path.join(TAB_DIR, "city_outcome_profiles.csv"))
    anom = pd.read_csv(os.path.join(TAB_DIR, "tab_pop_anomaly.csv"))
    eac = pd.read_csv(os.path.join(TAB_DIR, "tab_existing_ac_by_city.csv"))
    ca = pd.read_csv(os.path.join(MASSELOT_DIR, "results", "cityage.csv"))
    md = pd.read_csv(os.path.join(MASSELOT_DIR, "metadata.csv"))

    old = ca[ca.agegroup.isin(OLD_BANDS)]
    g = old.groupby("URAU_CODE").apply(lambda x: pd.Series({
        "mmt65": np.average(x.mmt, weights=x.agepop),
        "dr65": np.average(x.deathrate, weights=x.agepop) * 1e5,
        "rrheat65": np.average(x.rr_heat, weights=x.agepop)}), include_groups=False).reset_index()
    g = g.merge(md[["URAU_CODE", "prop_65p"]], on="URAU_CODE")
    g["share65"] = g.prop_65p * 100

    d = meta[["city", "city_label", "urau_code", "climate_cluster", "warmseason_mean_t2m"]].merge(
        g, left_on="urau_code", right_on="URAU_CODE")
    keep = ["city", "mort_100k", "pop_k", "coef_lst_coverage_pct"] + [
        c for c in prof.columns if c.startswith(("red_", "av100k_", "pvcap_", "cpd_"))]
    d = d.merge(prof[[c for c in keep if c in prof.columns]], on="city")
    a = anom.rename(columns={"City": "city_label", "Anomaly, May-Sep (C)": "anom"})[["city_label", "anom"]]
    a["anom"] = a.anom.astype(str).str.replace("+", "", regex=False).astype(float)
    e = eac.rename(columns={"City": "city_label", "AC coverage (%)": "ac0"})[["city_label", "ac0"]]
    d = d.merge(a, on="city_label").merge(e, on="city_label")
    if len(d) != len(meta):
        lost = sorted(set(meta.city_label) - set(d.city_label))
        raise SystemExit(f"descriptors missing for {lost}")
    return d


def r2(y: np.ndarray, d: pd.DataFrame, cols: list[str]) -> float:
    X = np.column_stack([np.ones(len(y))] + [d[c].to_numpy(float) for c in cols])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return 1 - (y - X @ b).var() / y.var()


def shapley(y: np.ndarray, d: pd.DataFrame, players: dict[str, list[str]]) -> dict[str, float]:
    """LMG shares: each player's R^2 gain averaged over all orderings (players may be groups)."""
    names, k = list(players), len(players)
    out = dict.fromkeys(names, 0.0)
    for j in names:
        others = [n for n in names if n != j]
        for s in range(k):
            w = math.factorial(s) * math.factorial(k - s - 1) / math.factorial(k)
            for S in itertools.combinations(others, s):
                base = [c for n in S for c in players[n]]
                out[j] += w * (r2(y, d, base + players[j]) - r2(y, d, base))
    return out


def cost_decomposition(d: pd.DataFrame, p: str) -> dict:
    s = d[np.isfinite(d[f"cpd_{p}"]) & (d[f"av100k_{p}"] > 0)]
    lc = np.log(s[f"cpd_{p}"])
    burden = s[f"av100k_{p}"] / (s[f"red_{p}"] / 100)      # 25-year baseline deaths per 100k
    terms = {"cost": np.log(s[f"pvcap_{p}"]), "burden": -np.log(burden), "share": -np.log(s[f"red_{p}"])}
    v = lc.var(ddof=1)
    out = {k: np.cov(t, lc)[0, 1] / v for k, t in terms.items()}
    assert abs(sum(out.values()) - 1) < 1e-9, out
    out.update(n=len(s), sd=lc.std(ddof=1), excluded=sorted(set(d.city_label) - set(s.city_label)))
    return out


def pct(x: float) -> str:
    v = round(100 * x)
    return f"${v}$\\,\\%" if v < 0 else f"{v}\\,\\%"


def dr2(x: float) -> str:
    return f"{x:.2f}" if x >= 0.01 else f"{x:.3f}"


def names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


CAPTION = (
    r"Drivers of cross-city differences in baseline heat mortality and in the cost per avoided death of each "
    r"pathway. \textbf{a}, Shapley decomposition of the explained variance of log baseline (2020) "
    r"heat-attributable mortality per 100\,000 across the <N> cities, regressed on the listed descriptors "
    r"($R^2=<R2>$; warm-season temperature alone, $R^2=<R2T>$). Each share is the descriptor's average "
    r"marginal contribution to $R^2$ over all orderings, so shares sum to 100\,\%; variance that descriptors "
    r"share (for example existing cooling and temperature, $r=<RAC>$) is split between them. The unique "
    r"contribution is the loss in $R^2$ when the group is dropped. Warm-season temperature and the existing "
    r"cooling coverage are those used in the main text; the heat-island anomaly is the May-September "
    r"population-weighted anomaly of Supplementary Table~\ref{tab:pop_anomaly}; the over-65 share, death "
    r"rate, minimum-mortality temperature (MMT) and heat relative risk are the city-level data of the source "
    r"study \citesupp{Masselot2023_SI} from which the exposure-response functions are built, "
    r"population-weighted over the 65-74, 75-84 and 85+ bands. \textbf{b}, Exact decomposition of the "
    r"cross-city variance of the log cost per avoided death into its three factors, "
    r"$\log(\mathrm{cost}/\mathrm{death}) = \log(\mathrm{cost\ per\ capita}) - "
    r"\log(\mathrm{baseline\ burden\ per\ capita}) - \log(\mathrm{share\ of\ burden\ avoided})$, each "
    r"term's share being its covariance with the total divided by the total variance; shares sum to "
    r"100\,\% and a negative share offsets the others.<EXCL>"
)


def write_table(n: int, r2_all: float, r2_t: float, r_ac: float, ind: dict, grp: dict,
                uniq: dict, cost: dict) -> str:
    excl = cost["Trees"]["excluded"]
    cap = (CAPTION.replace("<N>", str(n)).replace("<R2>", f"{r2_all:.2f}").replace("<R2T>", f"{r2_t:.2f}")
           .replace("<RAC>", f"{r_ac:.2f}")
           .replace("<EXCL>", f" Street trees exclude {names(excl)}, where greening avoids no deaths." if excl else ""))
    L = ["\\begin{table}[H]", "\\centering", "\\scriptsize", f"\\caption{{{cap}}}", "\\label{tab:drivers}",
         "\\begin{tabular}{llrr}",
         f"\\multicolumn{{4}}{{l}}{{\\textbf{{a}}\\quad Log baseline heat mortality per 100\\,000 ({n} cities)}} \\\\",
         "\\toprule", "Group & Descriptor & Share of explained variance & Unique $\\Delta R^2$ (group) \\\\",
         "\\midrule"]
    for gi, (gname, cols) in enumerate(GROUPS.items()):
        if gi:
            L.append("\\addlinespace")
        for ci, (col, label) in enumerate(cols):
            if len(cols) == 1:
                last = dr2(uniq[gname])
            else:
                last = f"\\multirow{{{len(cols)}}}{{*}}{{{dr2(uniq[gname])}}}" if ci == 0 else ""
            L.append(f"{gname if ci == 0 else ''} & {label} & {pct(ind[col] / r2_all)} & {last}".rstrip() + " \\\\")
    shares = " / ".join(str(round(100 * grp[g] / r2_all)) for g in GROUPS)
    L += ["\\midrule",
          "\\multicolumn{2}{l}{Groups: hazard / demography / exposure-response / existing AC} & "
          f"\\multicolumn{{2}}{{l}}{{{shares}\\,\\%}} \\\\",
          "\\bottomrule", "\\end{tabular}", "", "\\vspace{1em}", "",
          "\\begin{tabular}{lrrrr}",
          "\\multicolumn{5}{l}{\\textbf{b}\\quad Log present-value cost per avoided death} \\\\",
          "\\toprule", "Pathway & SD of log & Cost per capita & Baseline burden & Share of burden avoided \\\\",
          "\\midrule"]
    for p, label in PATHWAYS.items():
        c = cost[p]
        L.append(f"{label.format(n=c['n'])} & {c['sd']:.2f} & {pct(c['cost'])} & {pct(c['burden'])} & "
                 f"{pct(c['share'])} \\\\")
    L += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(L)


def hot_cool(d: pd.DataFrame) -> None:
    print("\nc. Hot versus cool, medians over cities where the pathway avoids deaths")
    for p in PATHWAYS:
        s = d[np.isfinite(d[f"cpd_{p}"]) & (d[f"av100k_{p}"] > 0)]
        med = s.groupby("climate_cluster")[[f"cpd_{p}", f"pvcap_{p}", f"av100k_{p}"]].median().reindex(CLASSES)
        r = med.loc["Cool", f"cpd_{p}"] / med.loc["Hot", f"cpd_{p}"]
        print(f"   {p:5s} cost per death (EUR k) hot {med.loc['Hot', f'cpd_{p}'] / 1e3:,.0f}, "
              f"cool {med.loc['Cool', f'cpd_{p}'] / 1e3:,.0f} (x{r:.1f}); spend per capita hot "
              f"{med.loc['Hot', f'pvcap_{p}']:,.0f}, cool {med.loc['Cool', f'pvcap_{p}']:,.0f} EUR")

    rows = []
    for _, r in d.iterrows():
        f = os.path.join(OUT_TREE, r.city, f"{r.city}_veg_diagnostics.json")
        if os.path.exists(f):
            v = json.load(open(f, encoding="utf-8"))
            rows.append(dict(city=r.city, cl=r.climate_cluster, av=r.av100k_Trees, pvcap=r.pvcap_Trees,
                             cpd=r.cpd_Trees, dgvi=v.get("citywide_dGVI_popw"),
                             dT=-(v.get("dT2M_summer_approx_popw") or np.nan)))
    if not rows:
        print(f"   [skip] no vegetation diagnostics under {OUT_TREE}")
        return
    t = pd.DataFrame(rows)
    t = t[np.isfinite(t.cpd) & (t.av > 0)]
    t["av_yr"] = t.av / 25                       # deaths per 100k per year
    t["av_per_K_yr"] = t.av_yr / t.dT            # ... per degree of population-weighted summer cooling
    med = t.groupby("cl")[["pvcap", "dgvi", "dT", "av_per_K_yr", "av_yr"]].median().reindex(CLASSES)
    print(f"   Trees physical chain ({len(t)} cities with diagnostics):")
    print(med.rename(columns={"pvcap": "EUR/cap", "dgvi": "dGVI popw", "dT": "cooling K",
                              "av_per_K_yr": "deaths/100k/yr/K", "av_yr": "deaths/100k/yr"}).round(3).to_string())


def main() -> int:
    d = city_data()
    y = np.log(d.mort_100k.to_numpy(float))
    cols = [c for g in GROUPS.values() for c, _ in g]
    r2_all, r2_t = r2(y, d, cols), r2(y, d, ["warmseason_mean_t2m"])
    ind = shapley(y, d, {c: [c] for c in cols})
    grp = shapley(y, d, {g: [c for c, _ in v] for g, v in GROUPS.items()})
    uniq = {g: r2_all - r2(y, d, [c for h, v in GROUPS.items() if h != g for c, _ in v]) for g in GROUPS}
    r_ac = float(np.corrcoef(d.ac0, d.warmseason_mean_t2m)[0, 1])
    cost = {p: cost_decomposition(d, p) for p in PATHWAYS}

    print(f"[inputs] {TAB_DIR}  ({len(d)} cities)")
    print(f"a. log baseline mortality per 100k: R2 = {r2_all:.3f} (temperature alone {r2_t:.3f}); "
          f"r(AC coverage, temperature) = {r_ac:.2f}")
    for g, v in GROUPS.items():
        print(f"   {g:20s} {100 * grp[g] / r2_all:4.0f}%   unique dR2 {uniq[g]:.3f}")
        for c, label in v:
            print(f"      {label:34s} {100 * ind[c] / r2_all:4.0f}%")
    print("b. share of cross-city variance in log cost per avoided death")
    for p, c in cost.items():
        print(f"   {p:5s} n={c['n']:2d} sd={c['sd']:.2f}  cost {c['cost']:+.2f}  burden {c['burden']:+.2f}  "
              f"share avoided {c['share']:+.2f}" + (f"  (excluded: {', '.join(c['excluded'])})" if c["excluded"] else ""))
    for lab in ["Ljubljana", "Nantes", "Athens", "Copenhagen"]:
        r = d[d.city_label == lab]
        if len(r):
            r = r.iloc[0]
            print(f"   {lab:11s} warm-season {r.warmseason_mean_t2m:.1f} C, baseline mortality {r.mort_100k:.1f}/100k, "
                  f"65+ MMT {r.mmt65:.1f} C")
    hot_cool(d)

    os.makedirs(TAB_DIR, exist_ok=True)
    tex = write_table(len(d), r2_all, r2_t, r_ac, ind, grp, uniq, cost)
    with open(os.path.join(TAB_DIR, "tab_drivers.tex"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tex)
    rows = [dict(panel="a", group=g, item=label, share=ind[c] / r2_all, unique_dR2=uniq[g], group_share=grp[g] / r2_all)
            for g, v in GROUPS.items() for c, label in v]
    rows += [dict(panel="b", group=p, item=term, share=cost[p][term], n=cost[p]["n"], sd_log=cost[p]["sd"])
             for p in PATHWAYS for term in ("cost", "burden", "share")]
    pd.DataFrame(rows).to_csv(os.path.join(TAB_DIR, "tab_drivers.csv"), index=False)
    d.to_csv(os.path.join(TAB_DIR, "tab_drivers_city_data.csv"), index=False)
    print(f"[saved] {os.path.join(TAB_DIR, 'tab_drivers.tex')} (+ .csv, _city_data.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

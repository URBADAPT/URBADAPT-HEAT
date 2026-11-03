"""Cross-city regression of each pathway's cost per avoided death on city characteristics.

Asks *why* an option is cheap in one city and expensive in another, and in particular why street
trees beat household cooling in some cities and not in others. For each outcome

    AC        log present-value cost per avoided death of income-targeted AC (net of waste heat)
    Trees     log cost per avoided death of the street-tree programme
    EWS       log cost per avoided death of the heat-health early warning system
    Trees/AC  log of the ratio of the two (negative = trees cheaper)

it fits one OLS regression across cities on a common set of city descriptors and reports:

  * standardised effects: the % change in cost per avoided death for a one-standard-deviation
    increase in a continuous descriptor (HHEWS starting point: effect against the
    'counterfactual' class), with HC3 heteroscedasticity-robust standard errors;
  * the Shapley (LMG) share of R^2 of each descriptor, the HHEWS dummies scored as one player,
    as in panel a of the drivers table (drivers_decomposition.py);
  * R^2, adjusted R^2 and the leave-one-out predictive R^2 (Q^2 = 1 - PRESS / SST), which is the
    honest measure of fit with ~40 cities and ten regressors;
  * variance inflation factors, printed;
  * for Trees/AC, each city's fitted log ratio split into descriptor contributions (b_j * z_ij,
    relative to the sample mean), so the cities where trees win can be read one by one;
  * for Trees and Trees/AC, the same regressions on smaller descriptor sets and the bivariate
    Spearman correlations, printed only: ten regressors on 38 cities overfits, and these show
    which associations survive.

Descriptors (one row per city)
  Climate and heat burden   warm-season (May-Sep) mean 2 m temperature; log baseline (2020)
                            heat-attributable mortality per 100 000
  Existing adaptation       baseline AC coverage (%); population-weighted baseline Green View
                            Index of the greening regions; HHEWS starting point (marginal /
                            intermediate / counterfactual attribution, two dummies)
  City size and form        log population of the modelled area; compact built share (LCZ 1-3
                            as a share of built land)
  Prices                    national price-level index (EU27 = 100) that scales every unit cost
  Greening response         (Trees and Trees/AC only) log effective greening cooling coefficient:
                            summer land-surface cooling per Green View Index point added,
                            population-weighted change in LST over population-weighted change in
                            GVI from <city>_veg_diagnostics.json. That ratio is the city's
                            month-LCZ coefficients averaged with weights population x greening
                            added, i.e. the coefficient magnitude where trees are actually planted.
                            An unweighted average over LCZ-months is not used: a few rarely
                            greened classes carry implausible estimates (LCZ 3: -45.8 C per GVI
                            point in Cologne, -3.0 in Hamburg) that would dominate it. The median
                            retained coefficient, which is independent of how many LCZ-months
                            have one, is printed as a robustness check. The effective coefficient
                            counts LCZ-months without a retained estimate as zero cooling, as the
                            model does, so it still partly carries coefficient coverage.

Sample: trees and the ratio exclude cities where greening avoids no deaths (Madrid, Sevilla), the
same rule as the drivers table, so they use 38 cities against 40 for AC and EWS.

Inputs
  TAB_DIR    city_metadata.csv, city_outcome_profiles.csv, tab_existing_ac_by_city.csv,
             tab_ews_by_city.csv (default compare/v3c/tables, the build behind the manuscript;
             the in-repo tables/ folder is stale)
  RUN_TREE   run outputs holding <city>/tables/<city>_policy_comparison.csv (baseline GVI),
             <city>/<city>_veg_diagnostics.json (effective coefficient) and
             <city>/interim/<city>_coef_bridge_check.csv (median retained coefficient)
  CALIB      urban-heat/calibration/calibration_params.csv (price-level index)

Outputs (in TAB_DIR)
  tab_ce_regression.tex               SI table (label tab:ce_regression)
  tab_ce_regression.csv               one row per outcome x descriptor
  tab_ce_regression_robust.{tex,csv}  SI table (label tab:ce_regression_robust): tree outcomes on
                                      smaller descriptor sets
  tab_ce_regression_city_data.csv     the regression data, one row per city
  tab_ce_regression_city_contrib.csv  Trees/AC fitted contributions per city and descriptor

Usage:
    python ce_regression.py
    TAB_DIR=compare/v3c/tables RUN_TREE=<runs> python ce_regression.py
"""

from __future__ import annotations

import itertools
import json
import math
import os

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TAB_DIR = os.environ.get("TAB_DIR", os.path.join(HERE, "compare", "v3c", "tables"))
RUN_TREE = os.environ.get("RUN_TREE", r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\v3c_40")
CALIB = os.environ.get("CALIB", os.path.join(REPO, "urban-heat", "calibration", "calibration_params.csv"))

# Continuous descriptors: column -> (label, group). Order is table order.
CONT = {
    "t2m":      ("Warm-season mean temperature", "Climate and heat burden"),
    "log_mort": ("Baseline heat mortality per 100\\,000 (log)", "Climate and heat burden"),
    "ac0":      ("Baseline AC coverage", "Existing adaptation"),
    "gvi0":     ("Baseline Green View Index", "Existing adaptation"),
    "log_pop":  ("Population of modelled area (log)", "City size and form"),
    "compact":  ("Compact built share (LCZ 1-3)", "City size and form"),
    "pli":      ("National price level", "Prices"),
    "log_coef": ("Greening cooling coefficient (log)", "Greening response"),
}
TREE_ONLY = {"log_coef"}                                 # enters the Trees and Trees/AC regressions only
TREE_KEYS = {"Trees", "Trees/AC"}
EWS_DUMMIES = ["ews_intermediate", "ews_marginal"]      # reference: counterfactual
EWS_LABEL = ("HHEWS starting point", "Existing adaptation")
# Shapley players: each continuous descriptor alone, the two HHEWS dummies together.
PLAYERS = {c: [c] for c in CONT} | {"ews": EWS_DUMMIES}
ROW_ORDER = ["t2m", "log_mort", "ac0", "gvi0", "ews", "log_pop", "compact", "pli", "log_coef"]
LABELS = {c: v[0] for c, v in CONT.items()} | {"ews": EWS_LABEL[0]}
GROUP_OF = {c: v[1] for c, v in CONT.items()} | {"ews": EWS_LABEL[1]}

OUTCOMES = {
    "AC": "Air conditioning",
    "Trees": "Street trees",
    "EWS": "Warning systems",
    "Trees/AC": "Trees relative to AC",
}


def baseline_gvi(city: str) -> float:
    f = os.path.join(RUN_TREE, city, "tables", f"{city}_policy_comparison.csv")
    if not os.path.exists(f):
        return np.nan
    pc = pd.read_csv(f)
    return float(np.average(pc.GVI_baseline, weights=pc.population))


def effective_coef(city: str) -> float:
    """Summer LST cooling per GVI point added, population-weighted (C per GVI point, >= 0)."""
    f = os.path.join(RUN_TREE, city, f"{city}_veg_diagnostics.json")
    if not os.path.exists(f):
        return np.nan
    v = json.load(open(f, encoding="utf-8"))
    dgvi = v.get("citywide_dGVI_popw") or 0.0
    return max(0.0, -v["dLST_summer_popw"] / dgvi) if dgvi > 0 else np.nan


def median_retained_coef(city: str) -> float:
    """Median summer (May-Sep) cooling coefficient over LCZ-months with a retained estimate."""
    f = os.path.join(RUN_TREE, city, "interim", f"{city}_coef_bridge_check.csv")
    if not os.path.exists(f):
        return np.nan
    b = pd.read_csv(f)
    s = b.loc[b.month.between(5, 9) & (b.coef_lst < 0), "coef_lst"]
    return float(-s.median()) if len(s) else np.nan


def city_data() -> pd.DataFrame:
    meta = pd.read_csv(os.path.join(TAB_DIR, "city_metadata.csv"))
    prof = pd.read_csv(os.path.join(TAB_DIR, "city_outcome_profiles.csv"))
    eac = pd.read_csv(os.path.join(TAB_DIR, "tab_existing_ac_by_city.csv"))
    ews = pd.read_csv(os.path.join(TAB_DIR, "tab_ews_by_city.csv"))
    cal = pd.read_csv(CALIB)

    d = meta[["city", "city_label", "country", "climate_cluster", "warmseason_mean_t2m", "pop_k_model",
              "lcz_compact_pct", "coef_lst_coverage_pct"]].merge(
        prof[["city", "mort_100k", "cpd_AC", "cpd_Trees", "cpd_EWS", "av100k_Trees"]], on="city")
    d = d.merge(eac.rename(columns={"City": "city_label", "AC coverage (%)": "ac0"})[["city_label", "ac0"]],
                on="city_label")
    d = d.merge(ews.rename(columns={"City": "city_label", "Attribution": "ews_class"})[["city_label", "ews_class"]],
                on="city_label")
    d = d.merge(cal[["city", "PLI"]], on="city")
    d["gvi0"] = [baseline_gvi(c) for c in d.city]
    d["coef_eff"] = [effective_coef(c) for c in d.city]
    d["coef_med"] = [median_retained_coef(c) for c in d.city]
    if len(d) != len(meta) or d.gvi0.isna().any():
        lost = sorted(set(meta.city_label) - set(d.city_label)) + list(d.loc[d.gvi0.isna(), "city_label"])
        raise SystemExit(f"descriptors missing for {lost} (check TAB_DIR, RUN_TREE, CALIB)")

    d = d.rename(columns={"warmseason_mean_t2m": "t2m", "lcz_compact_pct": "compact",
                          "coef_lst_coverage_pct": "coef_cov", "PLI": "pli"})
    d["log_mort"] = np.log(d.mort_100k)
    d["log_pop"] = np.log(d.pop_k_model)
    # Zero only where greening avoids no deaths (Madrid, Sevilla), which the tree regressions exclude.
    d["log_coef"] = np.log(d.coef_eff.where(d.coef_eff > 0))
    d["log_coef_med"] = np.log(d.coef_med)
    for k in ("intermediate", "marginal"):
        d[f"ews_{k}"] = (d.ews_class == k).astype(float)
    trees_ok = np.isfinite(d.cpd_Trees) & (d.av100k_Trees > 0)
    d["y_AC"] = np.log(d.cpd_AC)
    d["y_EWS"] = np.log(d.cpd_EWS)
    d["y_Trees"] = np.where(trees_ok, np.log(d.cpd_Trees), np.nan)
    d["y_Trees/AC"] = d.y_Trees - d.y_AC
    return d


def design(s: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """Intercept plus the listed columns; continuous ones z-scored on the estimation sample."""
    out = [np.ones(len(s))]
    for c in cols:
        x = s[c].to_numpy(float)
        out.append(x if c in EWS_DUMMIES else (x - x.mean()) / x.std(ddof=0))
    return np.column_stack(out)


def players_for(key: str) -> dict[str, list[str]]:
    return {p: v for p, v in PLAYERS.items() if key in TREE_KEYS or p not in TREE_ONLY}


def r2(y: np.ndarray, s: pd.DataFrame, cols: list[str]) -> float:
    if not cols:
        return 0.0
    X = design(s, cols)
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return 1 - ((y - X @ b) ** 2).sum() / ((y - y.mean()) ** 2).sum()


def shapley(y: np.ndarray, s: pd.DataFrame, players: dict[str, list[str]]) -> dict[str, float]:
    names, k = list(players), len(players)
    cache: dict[frozenset, float] = {}

    def v(S: frozenset) -> float:
        if S not in cache:
            cache[S] = r2(y, s, [c for n in ROW_ORDER if n in S for c in players[n]])
        return cache[S]

    out = dict.fromkeys(names, 0.0)
    for j in names:
        others = [n for n in names if n != j]
        for m in range(k):
            w = math.factorial(m) * math.factorial(k - m - 1) / math.factorial(k)
            for S in itertools.combinations(others, m):
                S = frozenset(S)
                out[j] += w * (v(S | {j}) - v(S))
    return out


def fit(d: pd.DataFrame, key: str) -> dict:
    s = d[np.isfinite(d[f"y_{key}"])].reset_index(drop=True)
    y = s[f"y_{key}"].to_numpy(float)
    players = players_for(key)
    cols = [c for c in CONT if c in players] + EWS_DUMMIES
    X = design(s, cols)
    res = sm.OLS(y, X).fit(cov_type="HC3")
    h = res.get_influence().hat_matrix_diag
    press = (((y - res.fittedvalues) / (1 - h)) ** 2).sum()
    q2 = 1 - press / ((y - y.mean()) ** 2).sum()
    vif = {c: variance_inflation_factor(X, i + 1) for i, c in enumerate(cols)}
    coef = dict(zip(["const"] + cols, res.params))
    se = dict(zip(["const"] + cols, res.bse))
    pv = dict(zip(["const"] + cols, res.pvalues))
    # Joint robust Wald test for the two HHEWS dummies.
    R = np.zeros((2, X.shape[1]))
    R[0, 1 + cols.index("ews_intermediate")] = 1
    R[1, 1 + cols.index("ews_marginal")] = 1
    p_ews = float(res.wald_test(R, scalar=True).pvalue)
    return dict(s=s, y=y, X=X, cols=cols, res=res, coef=coef, se=se, p=pv, p_ews=p_ews, vif=vif,
                r2=res.rsquared, adj=res.rsquared_adj, q2=q2, n=len(s), shap=shapley(y, s, players))


def pct_effect(b: float) -> float:
    return 100 * (math.exp(b) - 1)


def stars(p: float) -> str:
    return "^{***}" if p < 0.01 else "^{**}" if p < 0.05 else "^{*}" if p < 0.1 else ""


def cell(f: dict, player: str) -> str:
    if player not in f["shap"]:
        return "--"
    share = 100 * f["shap"][player] / f["r2"]
    if player == "ews":
        p = f["p_ews"]
        return f"{share:.0f}\\,\\% ($p{'<0.01' if p < 0.01 else f'={p:.2f}'}$)"
    e = pct_effect(f["coef"][player])
    return f"{share:.0f}\\,\\% (${e:+.0f}{stars(f['p'][player])}$)"


CAPTION = (
    r"Regression of each pathway's cost per avoided death on city characteristics. Each column is one "
    r"ordinary least-squares regression across cities of the log present-value cost per avoided death "
    r"(last column: log of the ratio of the tree to the AC cost per avoided death, negative where trees are "
    r"cheaper) on the descriptors listed. Each cell gives the descriptor's Shapley share of the explained "
    r"variance, its average marginal contribution to $R^2$ over all orderings, so that a column sums to "
    r"100\,\%; in brackets, the percentage change in cost per avoided death (in the ratio, last column) for "
    r"a one-standard-deviation increase in the descriptor, with heteroscedasticity-robust (HC3) "
    r"significance: $^{*}p<0.1$, $^{**}p<0.05$, $^{***}p<0.01$. The HHEWS starting point (marginal, "
    r"intermediate or counterfactual attribution) enters as two indicators scored as one Shapley player, "
    r"and its bracket gives the $p$-value of their joint robust Wald test. The leave-one-out $R^2$ refits "
    r"each regression without one city at a time and scores the prediction for that city. Baseline heat "
    r"mortality is the 2020 rate per 100\,000; the Green View Index is population-weighted over the "
    r"greening regions; the national price level (EU27 $=100$) scales every unit cost. The greening "
    r"cooling coefficient, which enters the tree regressions only, is the summer land-surface cooling per "
    r"Green View Index point added, population-weighted, i.e. the city's month- and Local-Climate-Zone-specific "
    r"coefficients averaged with weights given by population and greening added. Trees and the ratio exclude "
    r"<EXCL>, where greening avoids no deaths. Supplementary Table~\ref{tab:ce_regression_robust} repeats the "
    r"tree regressions with fewer descriptors."
)


def write_table(fits: dict, excl: list[str]) -> str:
    excl_txt = excl[0] if len(excl) == 1 else ", ".join(excl[:-1]) + " and " + excl[-1]
    L = ["\\begin{table}[H]", "\\centering", "\\scriptsize",
         f"\\caption{{{CAPTION.replace('<EXCL>', excl_txt)}}}", "\\label{tab:ce_regression}",
         "\\begin{tabular}{ll" + "r" * len(OUTCOMES) + "}", "\\toprule",
         "Group & Descriptor & " + " & ".join(OUTCOMES.values()) + " \\\\", "\\midrule"]
    prev = None
    for p in ROW_ORDER:
        g = GROUP_OF[p]
        if prev is not None and g != prev:
            L.append("\\addlinespace")
        L.append(f"{g if g != prev else ''} & {LABELS[p]} & " + " & ".join(cell(fits[k], p) for k in OUTCOMES) + " \\\\")
        prev = g
    L += ["\\midrule",
          "\\multicolumn{2}{l}{Cities} & " + " & ".join(str(fits[k]["n"]) for k in OUTCOMES) + " \\\\",
          "\\multicolumn{2}{l}{$R^2$ (adjusted)} & " + " & ".join(
              f"{fits[k]['r2']:.2f} ({fits[k]['adj']:.2f})" for k in OUTCOMES) + " \\\\",
          "\\multicolumn{2}{l}{Leave-one-out $R^2$} & " + " & ".join(f"{fits[k]['q2']:.2f}" for k in OUTCOMES) + " \\\\",
          "\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(L)


def print_fit(key: str, f: dict) -> None:
    print(f"\n{key}: n={f['n']}  R2={f['r2']:.2f}  adj={f['adj']:.2f}  LOO R2={f['q2']:.2f}")
    print(f"   {'descriptor':44s} {'Shapley':>8s} {'effect/SD':>10s} {'p (HC3)':>8s} {'VIF':>5s}")
    for p in ROW_ORDER:
        if p not in f["shap"]:
            continue
        share = 100 * f["shap"][p] / f["r2"]
        if p == "ews":
            for c in EWS_DUMMIES:
                print(f"   {LABELS[p] + ' [' + c[4:] + ']':44s} {'':>8s} {pct_effect(f['coef'][c]):+9.0f}% "
                      f"{f['p'][c]:8.3f} {f['vif'][c]:5.1f}")
            print(f"   {LABELS[p] + ' (joint)':44s} {share:7.0f}% {'':>10s} {f['p_ews']:8.3f}")
        else:
            print(f"   {LABELS[p].replace(chr(92) + ',', ' '):44s} {share:7.0f}% {pct_effect(f['coef'][p]):+9.0f}% "
                  f"{f['p'][p]:8.3f} {f['vif'][p]:5.1f}")


def ratio_contributions(f: dict) -> pd.DataFrame:
    """Fitted log(Trees/AC) per city as intercept + sum of b_j * x_ij (continuous x z-scored)."""
    s, X, cols = f["s"], f["X"], f["cols"]
    b = f["res"].params
    contrib = pd.DataFrame(X[:, 1:] * b[1:], columns=cols)
    contrib["ews"] = contrib[EWS_DUMMIES].sum(axis=1) - (s[EWS_DUMMIES].mean().to_numpy() * b[[1 + cols.index(c) for c in EWS_DUMMIES]]).sum()
    contrib = contrib.drop(columns=EWS_DUMMIES)
    out = pd.concat([s[["city", "city_label", "climate_cluster"]], contrib[ROW_ORDER]], axis=1)
    out["fitted"] = f["res"].fittedvalues
    out["observed"] = f["y"]
    out["residual"] = out.observed - out.fitted
    out["trees_cheaper"] = out.observed < 0
    return out


def explain_ratio(c: pd.DataFrame) -> None:
    print("\nTrees/AC: cities where trees are cheaper (observed log ratio < 0), "
          "with the two descriptors pushing the ratio down most (contribution to the log ratio vs sample mean)")
    for _, r in c[c.trees_cheaper].sort_values("observed").iterrows():
        top = r[ROW_ORDER].astype(float).nsmallest(2)
        why = ", ".join(f"{LABELS[k].replace(chr(92) + ',', ' ')} {v:+.2f}" for k, v in top.items())
        print(f"   {r.city_label:11s} {r.climate_cluster:9s} ratio {math.exp(r.observed):.2f} "
              f"(fitted {math.exp(r.fitted):.2f})  <- {why}")
    miss = c[(~c.trees_cheaper) & (c.fitted < 0)]
    if len(miss):
        print("   predicted trees-cheaper but observed AC-cheaper: " +
              ", ".join(f"{r.city_label} (obs {math.exp(r.observed):.2f}, fit {math.exp(r.fitted):.2f})"
                        for _, r in miss.iterrows()))


PARSIMONIOUS = [["log_coef"], ["log_coef", "log_pop"], ["log_coef", "log_pop", "t2m", "gvi0"],
                ["log_coef_med"], ["log_coef_med", "log_pop", "t2m", "gvi0"],
                ["log_pop", "t2m", "gvi0"]]
ROBUST_COLS = {"log_coef": "Effective coefficient", "log_coef_med": "Median retained coefficient",
               "log_pop": "Population (log)", "t2m": "Warm-season temperature", "gvi0": "Baseline GVI"}


def parsimonious_checks(d: pd.DataFrame) -> list[dict]:
    """Trees and Trees/AC with few regressors, since ten on 38 cities overfits."""
    from scipy.stats import spearmanr
    print("\nRobustness for the tree outcomes: smaller descriptor sets (effect per SD, HC3 p) and Spearman rho")
    out = []
    for key in ("Trees", "Trees/AC"):
        s = d[np.isfinite(d[f"y_{key}"])].reset_index(drop=True)
        y = s[f"y_{key}"].to_numpy(float)
        for i, cols in enumerate(PARSIMONIOUS, 1):
            res = sm.OLS(y, design(s, cols)).fit(cov_type="HC3")
            h = res.get_influence().hat_matrix_diag
            q2 = 1 - (((y - res.fittedvalues) / (1 - h)) ** 2).sum() / ((y - y.mean()) ** 2).sum()
            out.append(dict(outcome=key, spec=i, n=len(s), r2=res.rsquared, loo_r2=q2,
                            **{f"b_{c}": b for c, b in zip(cols, res.params[1:])},
                            **{f"p_{c}": p for c, p in zip(cols, res.pvalues[1:])}))
            terms = "  ".join(f"{c} {pct_effect(b):+.0f}% (p={p:.3f})"
                              for c, b, p in zip(cols, res.params[1:], res.pvalues[1:]))
            print(f"   {key:8s} R2 {res.rsquared:.2f}  LOO {q2:+.2f}  | {terms}")
        rho = {c: spearmanr(y, s[c])[0] for c in ["log_coef", "log_coef_med", "log_pop", "t2m", "log_mort", "ac0",
                                                  "gvi0", "compact", "pli"]}
        print(f"   {key:8s} Spearman: " + "  ".join(f"{c} {v:+.2f}" for c, v in rho.items()))
    return out


CAPTION_ROBUST = (
    r"Robustness of the tree regressions of Supplementary Table~\ref{tab:ce_regression} to smaller "
    r"descriptor sets, since ten regressors on 38 cities can overfit. Each row is one ordinary least-squares "
    r"regression of the log cost per avoided death of street trees, or of the log ratio of the tree to the AC "
    r"cost per avoided death, on the descriptors with an entry. Entries are the percentage change for a "
    r"one-standard-deviation increase in the descriptor (of its logarithm for the coefficients and "
    r"population), with HC3 significance: $^{*}p<0.1$, $^{**}p<0.05$, $^{***}p<0.01$. The effective "
    r"cooling coefficient is the one used in Supplementary Table~\ref{tab:ce_regression}: summer "
    r"land-surface cooling per Green View Index point added, population-weighted, which counts month and "
    r"Local-Climate-Zone combinations without a retained estimate as zero cooling, as the model does. The "
    r"median retained coefficient is the median over the combinations that have one, and so does not depend "
    r"on how many do. Cities: <N>."
)


def write_robust_table(rows: list[dict]) -> str:
    cols = list(ROBUST_COLS)
    head = "Outcome & " + " & ".join(ROBUST_COLS.values()) + " & $R^2$ & Leave-one-out $R^2$ \\\\"
    L = ["\\begin{table}[H]", "\\centering", "\\scriptsize",
         f"\\caption{{{CAPTION_ROBUST.replace('<N>', str(rows[0]['n']))}}}", "\\label{tab:ce_regression_robust}",
         "\\begin{tabular}{l" + "r" * (len(cols) + 2) + "}", "\\toprule", head, "\\midrule"]
    prev = None
    for r in rows:
        if prev is not None and r["outcome"] != prev:
            L.append("\\addlinespace")
        label = OUTCOMES[r["outcome"]] if r["outcome"] != prev else ""
        cells = [f"${pct_effect(r['b_' + c]):+.0f}{stars(r['p_' + c])}$" if f"b_{c}" in r else "" for c in cols]
        L.append(f"{label} & " + " & ".join(cells) + f" & {r['r2']:.2f} & {r['loo_r2']:.2f} \\\\")
        prev = r["outcome"]
    L += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(L)


def main() -> int:
    d = city_data()
    print(f"[inputs] {TAB_DIR}  ({len(d)} cities); baseline GVI from {RUN_TREE}; PLI from {CALIB}")
    fits = {k: fit(d, k) for k in OUTCOMES}
    for k, f in fits.items():
        print_fit(k, f)
    excl = sorted(d.loc[~np.isfinite(d["y_Trees"]), "city_label"])
    contrib = ratio_contributions(fits["Trees/AC"])
    explain_ratio(contrib)
    robust = parsimonious_checks(d)

    tex = write_table(fits, excl)
    with open(os.path.join(TAB_DIR, "tab_ce_regression.tex"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tex)
    with open(os.path.join(TAB_DIR, "tab_ce_regression_robust.tex"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(write_robust_table(robust))
    pd.DataFrame(robust).to_csv(os.path.join(TAB_DIR, "tab_ce_regression_robust.csv"), index=False)
    rows = []
    for k, f in fits.items():
        for p in [q for q in ROW_ORDER if q in f["shap"]]:
            for c in PLAYERS[p]:
                rows.append(dict(outcome=k, descriptor=c, group=GROUP_OF[p], label=LABELS[p],
                                 shapley_share=f["shap"][p] / f["r2"], coef_log=f["coef"][c],
                                 effect_pct=pct_effect(f["coef"][c]), se_hc3=f["se"][c], p_hc3=f["p"][c],
                                 p_joint_ews=f["p_ews"] if p == "ews" else np.nan, vif=f["vif"][c],
                                 n=f["n"], r2=f["r2"], adj_r2=f["adj"], loo_r2=f["q2"]))
    pd.DataFrame(rows).to_csv(os.path.join(TAB_DIR, "tab_ce_regression.csv"), index=False)
    keep = ["city", "city_label", "country", "climate_cluster", "cpd_AC", "cpd_Trees", "cpd_EWS",
            "y_AC", "y_Trees", "y_EWS", "y_Trees/AC", "t2m", "mort_100k", "log_mort", "ac0", "gvi0",
            "ews_class", "pop_k_model", "log_pop", "compact", "pli", "coef_eff", "log_coef", "coef_med"]
    d[keep].to_csv(os.path.join(TAB_DIR, "tab_ce_regression_city_data.csv"), index=False)
    contrib.to_csv(os.path.join(TAB_DIR, "tab_ce_regression_city_contrib.csv"), index=False)
    print(f"\n[saved] {os.path.join(TAB_DIR, 'tab_ce_regression.tex')} (+ .csv, _robust.tex/.csv, _city_data.csv, _city_contrib.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

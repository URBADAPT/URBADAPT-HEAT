"""Drivers of the two targeting results (equity-efficiency of income-targeted AC, Fig 2b; greening
progressivity, Fig 2c), city by city.

AC (from the NB12 zone tables, tables/ac_zone_drivers_<city>.csv). Units k = (AC zone, anchor
year). AC attenuation is linear in coverage, so a policy P avoids exactly
    B^P = sum_k A_k * dc_k^P,     dc^P = max(c^P - c^base, 0),
with A the deaths avoided per unit of coverage, restricted to cells with an SVI quintile (as the
NB05 quintile file behind Fig 2b). With added users u^P = pop2020 * dc^P equal for both policies:
    efficiency change  Y2 = B^T / B^U - 1 = S * G_mu
        S     = 0.5 * sum |w^T - w^U|, w^P = u^P / sum u^P   share of added users placed differently
        G_mu  = (mu_gain - mu_loss) / mu_U, mu = A / pop2020 deaths avoided per added user, averaged
                over the units targeting adds users to (gain) and takes them from (loss)
    equity gain        Y1 = 100 * (share of B^T in the top-2 SVI quintiles - same for U) = S_v * G_tau
        S_v   = 0.5 * sum |v^T - v^U|, v^P = b^P / B^P        share of the benefit placed differently
        G_tau = tau_gain - tau_loss, tau = (A_q4 + A_q5) / A  top-2 share of each unit's response
Gaining and losing units are also compared by summer mean T2M, share aged 65+ and SVI; income rank
is the rank of baseline AC coverage (downscaled with a logistic curve in income rank).

Greening (from tables/<city>_policy_comparison.csv). Uniform increment d = max(Q3 - GVI, 0);
vulnerability-weighted e = c * SVI on the regions below Q3, same total. The OLS slopes on SVI are
    slope_d = rho(d, SVI) * sd(d) / sd(SVI),   slope_e = c * cov(SVI * needy, SVI) / var(SVI),
and if every region were below Q3, slope_e = c = mean(d) / mean(SVI), so the weighted policy is
steeper where rho(d, SVI) * CV(d) < CV(SVI).

Usage:
  python targeting_drivers.py --zones <tree with NB12 tables> --tree <run tree> --out <TAB_DIR>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

TOP2 = ("A_q4", "A_q5")


def wmean(x, w):
    x, w = np.asarray(x, float), np.asarray(w, float)
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    return float(np.sum(x[ok] * w[ok]) / np.sum(w[ok])) if ok.any() else np.nan


def wspearman(x, y, w):
    x, y, w = (np.asarray(v, float) for v in (x, y, w))
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(w) & (w > 0)
    if ok.sum() < 3:
        return np.nan
    rx, ry, w = pd.Series(x[ok]).rank().to_numpy(), pd.Series(y[ok]).rank().to_numpy(), w[ok]
    mx, my = wmean(rx, w), wmean(ry, w)
    cov = np.sum(w * (rx - mx) * (ry - my))
    vx, vy = np.sum(w * (rx - mx) ** 2), np.sum(w * (ry - my) ** 2)
    return float(cov / np.sqrt(vx * vy)) if vx > 0 and vy > 0 else np.nan


def ac_city(city: str, z: pd.DataFrame) -> dict:
    A = z[[f"A_q{q}" for q in range(1, 6)]].sum(axis=1).to_numpy(float)
    A2 = z[list(TOP2)].sum(axis=1).to_numpy(float)
    dT = np.nan_to_num(np.clip((z["ac_income"] - z["ac_base"]).to_numpy(float), 0, None))
    dU = np.nan_to_num(np.clip((z["ac_uniform"] - z["ac_base"]).to_numpy(float), 0, None))
    pop0 = z["pop_2020"].to_numpy(float)
    uT, uU, bT, bU = pop0 * dT, pop0 * dU, A * dT, A * dU
    BT, BU = bT.sum(), bU.sum()
    wT, wU = uT / uT.sum(), uU / uU.sum()
    gain, loss = np.clip(wT - wU, 0, None), np.clip(wU - wT, 0, None)
    mu = np.where(pop0 > 0, A / pop0, np.nan)
    vT, vU = bT / BT, bU / BU
    vg, vl = np.clip(vT - vU, 0, None), np.clip(vU - vT, 0, None)
    tau = np.where(A > 0, A2 / A, np.nan)
    share65 = np.where(z["pop"] > 0, z["pop_65p"] / z["pop"], np.nan)
    T, svi = z["t2m_summer_mean"].to_numpy(float), z["svi_2020"].to_numpy(float)
    # Efficiency counts every resident (all cells, as NB05's city totals and Fig 2b's efficiency
    # axis); the equity shares need the SVI quintiles, so they use the quintile-resolved cells.
    A_all = z["A"].to_numpy(float)
    mu_all = np.where(pop0 > 0, A_all / pop0, np.nan)
    eff_all = (A_all * dT).sum() / (A_all * dU).sum() - 1
    first = (z["year"] == z["year"].min()).to_numpy()
    z0, p0 = z[first], pop0[first]
    base0 = z0["ac_base"].to_numpy(float)
    return dict(
        city=city, n_zones=int(first.sum()),
        users_added_pct=100 * uU[first].sum() / p0.sum(),
        base_cov_pct=100 * wmean(base0, p0),
        pop_untargeted_pct=100 * p0[(z0["ac_income"] - z0["ac_base"]).to_numpy(float) <= 1e-9].sum() / p0.sum(),
        efficiency_change_pct=100 * eff_all,
        users_moved_pct=100 * 0.5 * np.abs(wT - wU).sum(),
        mortality_gap_pct=100 * (wmean(mu_all, gain) - wmean(mu_all, loss)) / wmean(mu_all, wU),
        equity_gain_pp=100 * ((A2 * dT).sum() / BT - (A2 * dU).sum() / BU),
        benefit_moved_pct=100 * 0.5 * np.abs(vT - vU).sum(),
        vulnerability_gap_pp=100 * (wmean(tau, vg) - wmean(tau, vl)),
        temperature_gap_c=wmean(T, gain) - wmean(T, loss),
        age65_gap_pp=100 * (wmean(share65, gain) - wmean(share65, loss)),
        svi_gap=wmean(svi, gain) - wmean(svi, loss),
        rho_income_svi=-wspearman(base0, z0["svi_2020"], p0),
        rho_income_t2m=-wspearman(base0, z0["t2m_summer_mean"], p0),
        rho_income_age65=-wspearman(base0, (z0["pop_65p"] / z0["pop"]).to_numpy(float), p0),
        users_ratio=float(uT.sum() / uU.sum()),
        efficiency_change_svi_cells_pct=100 * (BT / BU - 1),
        mortality_gap_svi_cells_pct=100 * (wmean(mu, gain) - wmean(mu, loss)) / wmean(mu, wU),
        svi_resolved_share_pct=100 * A.sum() / A_all.sum(),
    )


def greening_city(city: str, d: pd.DataFrame) -> dict | None:
    cols = {"SVI", "ΔGVI_uniform", "ΔGVI_equity"}
    if len(d) < 3 or not cols <= set(d.columns):
        return None
    svi, du, de = (d[c].to_numpy(float) for c in ("SVI", "ΔGVI_uniform", "ΔGVI_equity"))
    ok = np.isfinite(svi) & np.isfinite(du) & np.isfinite(de)
    svi, du, de = svi[ok], du[ok], de[ok]
    su, se = np.polyfit(svi, du, 1)[0], np.polyfit(svi, de, 1)[0]
    rho = float(np.corrcoef(du, svi)[0, 1]) if du.std() > 0 else np.nan
    cv_d = du.std() / du.mean() if du.mean() > 0 else np.nan
    cv_s = svi.std() / svi.mean()
    return dict(city=city, n_regions=int(ok.sum()), n_below_target=int((du > 1e-12).sum()),
                rho_deficit_svi=rho, cv_deficit=cv_d, cv_svi=cv_s,
                slope_uniform=su, slope_weighted=se, weighted_steeper=bool(se > su),
                criterion_weighted_steeper=bool(np.isfinite(rho) and rho * cv_d < cv_s))


def r2(y, x):
    ok = np.isfinite(y) & np.isfinite(x)
    return float(np.corrcoef(y[ok], x[ok])[0, 1] ** 2) if ok.sum() > 2 else np.nan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", required=True, type=Path, help="tree with <city>/tables/ac_zone_drivers_<city>.csv")
    ap.add_argument("--tree", required=True, type=Path, help="run tree with <city>/tables/<city>_policy_comparison.csv")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    ac, gr = [], []
    for c in sorted(p.name for p in a.tree.iterdir() if p.is_dir()):
        fz = a.zones / c / "tables" / f"ac_zone_drivers_{c}.csv"
        if fz.exists():
            ac.append(ac_city(c, pd.read_csv(fz)))
        fp = a.tree / c / "tables" / f"{c}_policy_comparison.csv"
        if fp.exists():
            g = greening_city(c, pd.read_csv(fp))
            if g:
                gr.append(g)
    ac, gr = pd.DataFrame(ac), pd.DataFrame(gr)
    a.out.mkdir(parents=True, exist_ok=True)
    ac.to_csv(a.out / "targeting_drivers_ac.csv", index=False)
    gr.to_csv(a.out / "targeting_drivers_greening.csv", index=False)

    s = {}
    if len(ac):
        y2, S, G = ac.efficiency_change_pct.to_numpy(), ac.users_moved_pct.to_numpy() / 100, ac.mortality_gap_pct.to_numpy() / 100
        y1, Sv, Gt = ac.equity_gain_pp.to_numpy(), ac.benefit_moved_pct.to_numpy() / 100, ac.vulnerability_gap_pp.to_numpy() / 100
        s["ac"] = dict(
            n=len(ac), n_efficiency_gain=int((y2 > 0).sum()),
            max_abs_identity_err_efficiency=float(np.nanmax(np.abs(100 * (ac.users_ratio * (1 + S * G) - 1) - y2))),
            max_abs_identity_err_equity=float(np.nanmax(np.abs(100 * Sv * Gt - y1))),
            r2_efficiency_from_users_moved=r2(y2, S * np.nanmedian(G)),
            r2_efficiency_from_mortality_gap=r2(y2, np.nanmedian(S) * G),
            r2_equity_from_benefit_moved=r2(y1, Sv * np.nanmedian(Gt)),
            r2_equity_from_vulnerability_gap=r2(y1, np.nanmedian(Sv) * Gt),
            sign_mortality_gap_matches_temperature_gap=int(np.sum(np.sign(G) == np.sign(ac.temperature_gap_c))),
            sign_mortality_gap_matches_age65_gap=int(np.sum(np.sign(G) == np.sign(ac.age65_gap_pp))),
            spearman={f"{x}|{y}": float(ac[[x, y]].corr("spearman").iloc[0, 1]) for x, y in [
                ("mortality_gap_pct", "temperature_gap_c"), ("mortality_gap_pct", "age65_gap_pp"),
                ("vulnerability_gap_pp", "rho_income_svi"), ("equity_gain_pp", "rho_income_svi"),
                ("users_moved_pct", "pop_untargeted_pct"), ("efficiency_change_pct", "mortality_gap_pct"),
                ("equity_gain_pp", "benefit_moved_pct")]},
            median=ac.drop(columns=["city"]).median(numeric_only=True).round(3).to_dict(),
            n_upper_left=int(((y2 > 0) & (y1 > 0)).sum()),
            efficiency_gain_temperature_gap_median=float(ac.loc[y2 > 0, "temperature_gap_c"].median()),
            n_efficiency_gain_warmer=int((ac.loc[y2 > 0, "temperature_gap_c"] > 0).sum()),
            sign_mismatch_cities=ac.loc[np.sign(G) != np.sign(ac.temperature_gap_c),
                                        ["city", "temperature_gap_c", "age65_gap_pp"]].round(2).to_dict("records"),
            svi_cells=dict(
                n_efficiency_gain=int((ac.efficiency_change_svi_cells_pct > 0).sum()),
                n_abs_diff_gt_1pp=int((np.abs(ac.efficiency_change_svi_cells_pct - ac.efficiency_change_pct) > 1).sum()),
                max_abs_diff_pp=float(np.nanmax(np.abs(ac.efficiency_change_svi_cells_pct - ac.efficiency_change_pct))),
            ),
        )
    if len(gr):
        s["greening"] = dict(
            n=len(gr), uniform_positive=int((gr.slope_uniform > 0).sum()),
            weighted_positive=int((gr.slope_weighted > 0).sum()), weighted_steeper=int(gr.weighted_steeper.sum()),
            criterion_correct=int((gr.criterion_weighted_steeper == gr.weighted_steeper).sum()),
            median=gr.drop(columns=["city"]).median(numeric_only=True).round(3).to_dict(),
        )
    (a.out / "targeting_drivers_summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()

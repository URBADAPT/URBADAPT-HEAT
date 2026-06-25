#!/usr/bin/env python3
"""Evaluation variants on the existing covariate table + labels (no covariate rebuild):

  A. Big-cities-only skill: leave-one-country-out per-city Spearman as we raise a
     minimum sub-unit (and population) threshold - small cities have noisy rank
     correlations (a Spearman on 5 zones is unstable), so this shows the "clean"
     skill alongside the all-cities number.
  B. Training-weight schemes: re-run leave-one-country-out under population /
     uniform / sqrt(pop) / equal-per-city / equal-per-country sample weights and
     report whether balancing the *training* contribution recovers France (and
     the other weak folds) without sinking the overall.
  C. Tail hit-rate: within each city, fraction of the observed top- and
     bottom-quintile units the model also places in that quintile (random ~0.20)
     - the extremes are what the downstream AC / heat-equity step cares about.

    python eval_variants.py --config config_urbadapt.yaml
"""
from __future__ import annotations
import argparse
import numpy as np
import pandas as pd
import yaml

from pipeline import schema, features as F, model as M, evaluate as E


def prep(cfg):
    c = cfg["columns"]
    df = pd.read_csv(cfg["io"]["subdivisions"], dtype={c["subdivision_id"]: str})
    labels = pd.read_csv(cfg["io"]["income_labels"], dtype={c["subdivision_id"]: str})
    keys = cfg["io"].get("merge_on", [c["city_id"], c["subdivision_id"]])
    cols = list(keys)
    for x in ["inc_rel_to_city_median", "inc_pct_within_city", c.get("group"), c["income"]]:
        if x and x in labels.columns and x not in cols:
            cols.append(x)
    df = df.merge(labels[cols].drop_duplicates(keys), on=keys, how="left")
    req = [x for x in cfg.get("filters", {}).get("require_non_null", []) if x in df.columns]
    if req:
        df = df[df[req].notna().all(axis=1)].copy()
    return df


def per_country_pooled(country, obs, pred, w):
    """Population-weighted Spearman per country (matches run_summary.per_group_skill)."""
    out = {}
    d = pd.DataFrame({"country": country, "obs": obs, "pred": pred, "w": w})
    for g, gg in d.groupby("country"):
        m = np.isfinite(gg["pred"]).values
        if m.sum() < 3:
            continue
        wv = gg["w"].values[m]
        out[g] = (E._spearman(gg["obs"].values[m], gg["pred"].values[m],
                              wv if wv.sum() > 0 else None), int(m.sum()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    cfg = yaml.safe_load(open(ap.parse_args().config))
    c = cfg["columns"]
    gcol = cfg["validation"]["group_column"]
    df = prep(cfg)
    train, _, _ = schema.split_train_predict(df, cfg)
    print(f"cohort: {len(train)} units, {train[c['city_id']].nunique()} cities, "
          f"{train[gcol].nunique()} countries\n")

    # baseline pop-weighted leave-one-country-out OOF (for A and C)
    per_fold, oof = E.cross_validate(train, cfg)
    oof[gcol] = train[gcol].values
    oof["city_pop"] = pd.to_numeric(train["city_population"], errors="coerce").values
    oof["n_city_units"] = oof.groupby(c["city_id"])[c["city_id"]].transform("size")

    # ---- A. big-cities-only -------------------------------------------------
    def percity_zw(o):
        rows = []
        for _, g in o.groupby(c["city_id"]):
            m = np.isfinite(g["index_pred"]).values
            if m.sum() < 3:
                continue
            w = g["__w"].values[m]
            rows.append((E._spearman(g["index_obs"].values[m], g["index_pred"].values[m],
                                     w if w.sum() > 0 else None), int(m.sum())))
        if not rows:
            return np.nan, 0, 0
        sp = np.array([r[0] for r in rows]); nz = np.array([r[1] for r in rows])
        return float(np.average(sp, weights=nz)), len(rows), int(nz.sum())

    print("=== A. Big-cities-only (leave-one-country-out, per-city zone-wtd Spearman) ===")
    print(f"{'min sub-units':>14s}{'n_cities':>10s}{'n_zones':>9s}{'spearman':>10s}")
    for t in [1, 5, 10, 15, 20, 40]:
        sp, nc, nz = percity_zw(oof[oof["n_city_units"] >= t])
        lab = f"{t}" + (" (all)" if t == 1 else "")
        print(f"{lab:>14s}{nc:>10d}{nz:>9d}{sp:>10.3f}")
    print(f"{'min city pop':>14s}{'n_cities':>10s}{'n_zones':>9s}{'spearman':>10s}")
    for p in [100_000, 250_000, 500_000]:
        sp, nc, nz = percity_zw(oof[oof["city_pop"] >= p])
        print(f"{p//1000:>11d}k{nc:>10d}{nz:>9d}{sp:>10.3f}")

    # ---- B. training-weight schemes ----------------------------------------
    print("\n=== B. Training-weight schemes (leave-one-country-out, per-country pooled Spearman) ===")
    X_all, _ = F.build_features(train, cfg)
    y_all = F.build_target(train, cfg).values
    pop = pd.to_numeric(train[c["population"]], errors="coerce").fillna(0.0).values
    city = train[c["city_id"]].values
    country = train[gcol].values
    backend = M.resolve_backend(cfg["model"]["backend"])

    def weights(scheme):
        if scheme == "population":   return pop
        if scheme == "uniform":      return np.ones_like(pop)
        if scheme == "sqrt_pop":     return np.sqrt(pop)
        s = pd.Series(pop).groupby(city if scheme == "equal_city" else country).transform("sum").values
        return np.where(s > 0, pop / s, 0.0)        # each city / country sums to 1

    idx_obs = F.target_to_index(y_all, cfg)
    show = ["FR", "IE", "NO", "PT", "CH", "IT", "BE"]
    print(f"{'scheme':>14s}{'overall_zw':>11s}" + "".join(f"{k:>7s}" for k in show))
    for scheme in ["population", "uniform", "sqrt_pop", "equal_city", "equal_country"]:
        w = weights(scheme)
        oofw = np.full(len(train), np.nan)
        for g in pd.unique(country):
            te = country == g
            mdl = M.make_model(backend, cfg["model"]["params"])
            oofw[te] = M.fit_predict(mdl, X_all.values[~te], y_all[~te], X_all.values[te], w[~te])
        res = per_country_pooled(country, idx_obs, F.target_to_index(oofw, cfg), pop)
        sp = np.array([v[0] for v in res.values()]); nz = np.array([v[1] for v in res.values()])
        overall = np.average(sp, weights=nz)
        row = f"{scheme:>14s}{overall:>11.3f}"
        for k in show:
            row += f"{res.get(k, (np.nan,))[0]:>7.3f}" if k in res else f"{'--':>7s}"
        print(row)

    # ---- C. tail hit-rate ---------------------------------------------------
    print("\n=== C. Within-city tail hit-rate (top/bottom quintile, pooled; random ~0.20) ===")
    th = tt = bh = bt = 0
    for _, g in oof.groupby(c["city_id"]):
        gg = g[np.isfinite(g["index_pred"])]
        n = len(gg)
        if n < 5:
            continue
        k = max(1, int(round(0.2 * n)))
        ot = set(gg["index_obs"].nlargest(k).index); pt = set(gg["index_pred"].nlargest(k).index)
        ob = set(gg["index_obs"].nsmallest(k).index); pb = set(gg["index_pred"].nsmallest(k).index)
        th += len(ot & pt); tt += len(ot); bh += len(ob & pb); bt += len(ob)
    print(f"  top-quintile (richest)  hit-rate: {th/tt:.3f}  ({tt} zones)")
    print(f"  bottom-quintile (poorest) hit-rate: {bh/bt:.3f}  ({bt} zones)")


if __name__ == "__main__":
    main()

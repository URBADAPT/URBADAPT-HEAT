#!/usr/bin/env python3
"""Deployment pass: predict a city's within-city income distribution from a boundary
file, training the emulator on every OTHER labelled city.

Builds covariates for the target boundary with covariates.py (the boundary's own
`city` column places it - no income label needed), fits the model on the labelled
training cities, and writes per-unit income_index_pred + p_inc. Pass --exclude to drop
the target's twin from training for an honest out-of-sample check; if the target city
is also in the income labels, a validation Spearman is printed.

  python deploy_predict.py --boundary data/boundaries/IT_Venice_CAPZONE_2025.gpkg \
      --layer IT_Venice_CAPZONE_2025 --key boundary_code --city Venezia --country IT \
      --exclude Venezia --config config_urbadapt.yaml
"""
from __future__ import annotations
import argparse
import copy
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

import covariates as C
from pipeline import schema, features as F, model as M


def target_covariates(cfg, boundary, layer, key, country):
    """Covariate table for one target boundary layer (reuses covariates.build)."""
    tcfg = copy.deepcopy(cfg)
    cs = tcfg["covariate_sources"]
    cs["boundaries_dir"] = str(Path(boundary).parent)
    cs["boundaries_manifest"] = [{"country": country, "file": Path(boundary).name,
                                  "layer": layer, "key_col": key}]
    tcfg.setdefault("io", {})["income_labels"] = None     # use the boundary's own city col
    tgt = C.build(tcfg)
    tgt["country"] = country                              # needed for the country one-hot
    return tgt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    ap.add_argument("--boundary", required=True)
    ap.add_argument("--layer", default=None)
    ap.add_argument("--key", default="boundary_code")
    ap.add_argument("--city", required=True)
    ap.add_argument("--country", required=True)
    ap.add_argument("--exclude", default=None, help="city to drop from training (honest OOS)")
    ap.add_argument("--out", default="results/deploy_predictions.csv")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    c = cfg["columns"]

    print(f"Building covariates for target '{args.city}' from {args.boundary} ...")
    tgt = target_covariates(cfg, args.boundary, args.layer, args.key, args.country)
    print(f"  {len(tgt)} target units")

    # training table (main covariates + labels), exactly as run.py assembles it
    df = pd.read_csv(cfg["io"]["subdivisions"], dtype={c["subdivision_id"]: str})
    lab = pd.read_csv(cfg["io"]["income_labels"], dtype={c["subdivision_id"]: str})
    keys = cfg["io"].get("merge_on", [c["city_id"], c["subdivision_id"]])
    cols = list(keys)
    for x in ["inc_rel_to_city_median", "inc_pct_within_city", c.get("group"), c["income"]]:
        if x and x in lab.columns and x not in cols:
            cols.append(x)
    df = df.merge(lab[cols].drop_duplicates(keys), on=keys, how="left")
    req = [x for x in cfg.get("filters", {}).get("require_non_null", []) if x in df.columns]
    if req:
        df = df[df[req].notna().all(axis=1)].copy()
    if args.exclude:
        df = df[df[c["city_id"]] != args.exclude].copy()
    train, _, train_cities = schema.split_train_predict(df, cfg)
    print(f"Train: {len(train_cities)} cities, {len(train)} zones"
          + (f"  (excluded {args.exclude})" if args.exclude else ""))

    backend = M.resolve_backend(cfg["model"]["backend"])
    X_tr, feat = F.build_features(train, cfg)
    y_tr = F.build_target(train, cfg).values
    w_tr = F.sample_weights(train, cfg)
    mdl = M.make_model(backend, cfg["model"]["params"])
    M.fit_predict(mdl, X_tr.values, y_tr, X_tr.values[:1], w_tr)

    Xt, _ = F.build_features(tgt, cfg)
    Xt = Xt.reindex(columns=feat, fill_value=0.0)
    idx = F.renormalise_index(tgt, F.target_to_index(mdl.predict(Xt.values), cfg), cfg)
    tgt["income_index_pred"] = idx
    tgt["p_inc"] = F.index_to_rank(tgt, idx, cfg)

    keep = [x for x in [c["city_id"], c["subdivision_id"], "pop_zone",
                        "income_index_pred", "p_inc"] if x in tgt.columns]
    out = tgt[keep].sort_values("p_inc").reset_index(drop=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Wrote -> {args.out}")

    # validation against observed labels for the (excluded) city, if available
    obs = lab[lab[c["city_id"]] == (args.exclude or args.city)]
    if len(obs):
        v = tgt[[c["subdivision_id"], "p_inc"]].merge(
            obs[[c["subdivision_id"], "inc_pct_within_city"]], on=c["subdivision_id"])
        v["obs"] = pd.to_numeric(v["inc_pct_within_city"], errors="coerce")
        v = v.dropna(subset=["obs"])
        if len(v) >= 3:
            sp = v["p_inc"].rank().corr(v["obs"].rank())
            mae = (v["p_inc"] - v["obs"]).abs().mean()
            print(f"\nValidation vs observed inc_pct_within_city ({len(v)} units): "
                  f"Spearman={sp:.3f}  MAE(p_inc)={mae:.3f}")
    print("\nlowest- and highest-income predicted units:")
    print(out.head(4).to_string(index=False))
    print(out.tail(4).to_string(index=False))


if __name__ == "__main__":
    main()

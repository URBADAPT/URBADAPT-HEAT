#!/usr/bin/env python3
"""Within-city income distribution emulator - command line entry point.

    python run.py --config config.yaml

Steps:
  1. Load the zone table (one row per sub-city zone).
  2. Split into TRAIN cities (have observed income) and PREDICT cities (don't).
  3. Cross-validate with leave-one-city-out to estimate transfer skill.
  4. Refit on all training cities and predict the relative income index
     (and p_inc rank) for every zone.
  5. Write predictions + CV report.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from pipeline import schema, features as F, model as M, evaluate as E


def load_table(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".gpkg", ".geojson", ".shp"):
        import geopandas as gpd
        return gpd.read_file(p).drop(columns="geometry", errors="ignore")
    return pd.read_csv(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    c = cfg["columns"]
    out_dir = Path(cfg["io"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_table(cfg["io"]["subdivisions"])
    df = schema.validate(df, cfg)
    train, predict, train_cities = schema.split_train_predict(df, cfg)
    print(f"Backend       : {M.resolve_backend(cfg['model']['backend'])}")
    print(f"Train cities  : {len(train_cities)}  ({len(train)} labelled zones)")
    print(f"Predict zones : {len(predict)}")

    # ---- 1. cross-validation (transfer skill) ---------------------------------
    per_fold, oof = E.cross_validate(train, cfg)
    if not per_fold.empty:
        wm = np.average(per_fold["spearman"], weights=per_fold["n_zones"])
        print("\nLeave-one-city-out CV (rank skill per held-out city):")
        print(per_fold.to_string(index=False))
        print(f"\n  zone-weighted mean Spearman = {wm:.3f}")
        per_fold.to_csv(out_dir / "cv_report.csv", index=False)
        oof.to_csv(out_dir / "cv_out_of_fold_predictions.csv", index=False)

    # ---- 2. refit on all training data ----------------------------------------
    backend = M.resolve_backend(cfg["model"]["backend"])
    X_tr, feat_names = F.build_features(train, cfg)
    y_tr = F.build_target(train, cfg).values
    w_tr = (pd.to_numeric(train[c["population"]], errors="coerce").fillna(0.0).values
            if cfg["model"]["weight_by_population"] else None)
    final = M.make_model(backend, cfg["model"]["params"])
    M.fit_predict(final, X_tr.values, y_tr, X_tr.values[:1], w_tr)  # fit

    # ---- 3. predict every zone (train + predict) ------------------------------
    full = df.copy()
    X_full, _ = F.build_features(full, cfg)
    X_full = X_full.reindex(columns=feat_names, fill_value=0.0)
    raw_pred = final.predict(X_full.values)
    idx = F.target_to_index(raw_pred, cfg)

    if cfg["postprocess"]["renormalise_to_reference"]:
        idx = F.renormalise_index(full, idx, cfg)

    full_out = full[[c["city_id"], c["subdivision_id"], c["population"]]].copy()
    full_out["income_index_pred"] = idx           # 1.0 == municipal pop-weighted mean
    if cfg["postprocess"].get("emit_rank", True):
        full_out["p_inc"] = F.index_to_rank(full, idx, cfg)
    full_out["income_obs"] = pd.to_numeric(full[c["income"]], errors="coerce").values
    full_out["is_training_city"] = full_out[c["city_id"]].isin(train_cities)

    full_out.to_csv(out_dir / "income_index_predictions.csv", index=False)

    summary = {
        "backend": backend,
        "n_train_cities": len(train_cities),
        "n_train_zones": int(len(train)),
        "n_predicted_zones": int(len(predict)),
        "features": feat_names,
        "cv_zone_weighted_spearman": (float(np.average(per_fold["spearman"],
                                       weights=per_fold["n_zones"]))
                                       if not per_fold.empty else None),
    }
    json.dump(summary, open(out_dir / "run_summary.json", "w"), indent=2)
    print(f"\nWrote -> {out_dir/'income_index_predictions.csv'}")
    print(f"Wrote -> {out_dir/'run_summary.json'}")


if __name__ == "__main__":
    main()

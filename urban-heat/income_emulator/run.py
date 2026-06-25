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
    # Optionally merge income labels (harmonized file) onto the covariate table.
    if cfg["io"].get("income_labels"):
        labels = load_table(cfg["io"]["income_labels"])
        keys = cfg["io"].get("merge_on", [c["city_id"], c["subdivision_id"]])
        # Dedupe: precomputed_column and precomputed_rank_column can be the same
        # (e.g. the rank target), which would otherwise merge the column twice.
        label_cols = list(keys)
        for x in [cfg["target"].get("precomputed_column"),
                  cfg["target"].get("precomputed_rank_column"),
                  c.get("group"), c["income"]]:
            if x and x in labels.columns and x not in label_cols:
                label_cols.append(x)
        df = df.merge(labels[label_cols].drop_duplicates(keys), on=keys, how="left")

    # Optional: drop units lacking required covariates (e.g. countries with no
    # census coverage). Such units would otherwise be median-imputed to a flat
    # within-city signal, adding noise to training and a meaningless CV fold.
    req = [col for col in cfg.get("filters", {}).get("require_non_null", []) if col in df.columns]
    if req:
        mask = df[req].notna().all(axis=1)
        n_drop = int((~mask).sum())
        if n_drop:
            grp = c.get("group")
            tag = (" | by %s: %s" % (grp, df.loc[~mask].groupby(grp).size().to_dict())
                   if grp and grp in df.columns else "")
            print(f"filter: dropped {n_drop}/{len(df)} units missing {req}{tag}")
            df = df[mask].copy()

    df = schema.validate(df, cfg)
    train, predict, train_cities = schema.split_train_predict(df, cfg)
    print(f"Backend       : {M.resolve_backend(cfg['model']['backend'])}")
    print(f"Train cities  : {len(train_cities)}  ({len(train)} labelled zones)")
    print(f"Predict zones : {len(predict)}")

    # ---- 1. cross-validation (transfer skill) ---------------------------------
    per_fold, oof = E.cross_validate(train, cfg)
    by_group = E.per_group_skill(oof, train, cfg)   # pooled by held-out country
    if not per_fold.empty:
        wm = np.average(per_fold["spearman"], weights=per_fold["n_zones"])
        scheme = cfg["validation"]["scheme"]
        print(f"\nCV ({scheme}) - per-city rank skill of held-out predictions:")
        print(per_fold.to_string(index=False))
        print(f"\n  zone-weighted mean Spearman = {wm:.3f}")
        per_fold.to_csv(out_dir / "cv_report.csv", index=False)
        oof.to_csv(out_dir / "cv_out_of_fold_predictions.csv", index=False)
    if not by_group.empty:
        gcol = by_group.columns[0]
        gwm = np.average(by_group["spearman"], weights=by_group["n_zones"])
        print(f"\nPer-{gcol} CV (pooled across each {gcol}'s zones):")
        print(by_group.to_string(index=False))
        print(f"\n  zone-weighted mean Spearman = {gwm:.3f}")
        by_group.to_csv(out_dir / "cv_report_by_country.csv", index=False)
    tail = E.tail_hit_rate(oof, cfg)
    if tail.get("top_hit_rate") is not None:
        print(f"\nWithin-city tail hit-rate (random ~{tail['quantile']}): "
              f"top {tail['top_hit_rate']:.3f} / bottom {tail['bottom_hit_rate']:.3f}")

    # ---- 2. refit on all training data ----------------------------------------
    backend = M.resolve_backend(cfg["model"]["backend"])
    X_tr, feat_names = F.build_features(train, cfg)
    y_tr = F.build_target(train, cfg).values
    w_tr = F.sample_weights(train, cfg)
    final = M.make_model(backend, cfg["model"]["params"])
    M.fit_predict(final, X_tr.values, y_tr, X_tr.values[:1], w_tr)  # fit

    # ---- 3. predict every zone (train + predict) ------------------------------
    full = df.copy()
    X_full, _ = F.build_features(full, cfg)
    X_full = X_full.reindex(columns=feat_names, fill_value=0.0)
    raw_pred = final.predict(X_full.values)
    idx = F.target_to_index(raw_pred, cfg)

    # Resolve renormalisation mode (back-compatible with old renormalise_to_reference).
    pp = cfg.setdefault("postprocess", {})
    if "renormalise_mode" not in pp:
        pp["renormalise_mode"] = "pop_weighted_mean" if pp.get("renormalise_to_reference") else "none"
    idx = F.renormalise_index(full, idx, cfg)

    keep = [c["city_id"], c["subdivision_id"]]
    if c.get("population") and c["population"] in full.columns:
        keep.append(c["population"])
    full_out = full[keep].copy()
    full_out["income_index_pred"] = idx           # 1.0 == per-city reference (median/mean)
    if pp.get("emit_rank", True):
        full_out["p_inc"] = F.index_to_rank(full, idx, cfg)
    tcol = cfg["target"].get("precomputed_column") or c["income"]
    full_out["target_obs"] = pd.to_numeric(full[tcol], errors="coerce").values
    full_out["is_training_city"] = full_out[c["city_id"]].isin(train_cities)

    full_out.to_csv(out_dir / "income_index_predictions.csv", index=False)

    summary = {
        "backend": backend,
        "cv_scheme": cfg["validation"]["scheme"],
        "n_train_cities": len(train_cities),
        "n_train_zones": int(len(train)),
        "n_predicted_zones": int(len(predict)),
        "features": feat_names,
        "cv_zone_weighted_spearman": (float(np.average(per_fold["spearman"],
                                       weights=per_fold["n_zones"]))
                                       if not per_fold.empty else None),
        "cv_by_country_zone_weighted_spearman": (float(np.average(by_group["spearman"],
                                       weights=by_group["n_zones"]))
                                       if not by_group.empty else None),
        "cv_by_country": (by_group.to_dict("records") if not by_group.empty else []),
        "cv_tail_hit_rate": tail,
    }
    json.dump(summary, open(out_dir / "run_summary.json", "w"), indent=2)
    print(f"\nWrote -> {out_dir/'income_index_predictions.csv'}")
    print(f"Wrote -> {out_dir/'run_summary.json'}")


if __name__ == "__main__":
    main()

"""leave-one-city-out validation for labelled cities.

For each requested city: train the emulator on every OTHER labelled city, predict
the held-out city, and score it against its observed within-city income percentile.
This is the realistic "new city in a sampled country" regime. Uses the canonical
pop-weighted Spearman (pipeline.evaluate._spearman), so numbers are comparable to
the cross_validate / cv_report metric.

    cd income_emulator
    python validation/leave_one_city_out.py --config config_urbadapt.yaml Roma Madrid Amsterdam
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from run import load_table                       # noqa: E402
from pipeline import schema, features as F, model as M, evaluate as E  # noqa: E402


def _assemble(cfg):
    c = cfg["columns"]
    df = load_table(cfg["io"]["subdivisions"])
    lab = load_table(cfg["io"]["income_labels"])
    keys = cfg["io"].get("merge_on", [c["city_id"], c["subdivision_id"]])
    cols = list(keys)
    for x in [cfg["target"].get("precomputed_column"), cfg["target"].get("precomputed_rank_column"),
              c.get("group"), c["income"]]:
        if x and x in lab.columns and x not in cols:
            cols.append(x)
    df = df.merge(lab[cols].drop_duplicates(keys), on=keys, how="left")
    req = [x for x in cfg.get("filters", {}).get("require_non_null", []) if x in df.columns]
    if req:
        df = df[df[req].notna().all(axis=1)].copy()
    return schema.validate(df, cfg), c


def leave_one_out(cfg, df, c, target):
    backend = M.resolve_backend(cfg["model"]["backend"])
    train = df[df[c["city_id"]] != target].copy()
    test = df[df[c["city_id"]] == target].copy()
    if test.empty:
        return None
    X_tr, feat = F.build_features(train, cfg)
    y_tr = F.build_target(train, cfg).values
    w_tr = F.sample_weights(train, cfg)
    mdl = M.make_model(backend, cfg["model"]["params"])
    M.fit_predict(mdl, X_tr.values, y_tr, X_tr.values[:1], w_tr)
    X_te, _ = F.build_features(test, cfg)
    X_te = X_te.reindex(columns=feat, fill_value=0.0)
    idx = F.target_to_index(mdl.predict(X_te.values), cfg)
    test["p_inc"] = F.index_to_rank(test, idx, cfg)
    test["obs"] = pd.to_numeric(test[cfg["target"]["precomputed_column"]], errors="coerce")
    pop = pd.to_numeric(test.get(c.get("population"), pd.Series(1.0, index=test.index)),
                        errors="coerce").fillna(0.0).values
    rho = E._spearman(test["obs"].values, test["p_inc"].values, pop)   # canonical pop-weighted

    def bq(col):  # poorest 20% by population
        o = test.sort_values(col); cum = o[c.get("population")].cumsum() / o[c.get("population")].sum()
        return set(o[cum <= 0.2][c["subdivision_id"]])
    bo, bp = bq("obs"), bq("p_inc")
    return dict(city=target, n=len(test), spearman=rho,
                poorest_q_hit=f"{len(bo & bp)}/{len(bo)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    ap.add_argument("cities", nargs="+")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    df, c = _assemble(cfg)
    print(f"{'city':14s} {'n':>4s} {'leave-one-city-out rho':>22s} {'poorest-quintile hit':>20s}")
    for city in a.cities:
        r = leave_one_out(cfg, df, c, city)
        if r is None:
            print(f"{city:14s}  (not a labelled city in {cfg['io']['subdivisions']})")
        else:
            print(f"{r['city']:14s} {r['n']:4d} {r['spearman']:22.3f} {r['poorest_q_hit']:>20s}")


if __name__ == "__main__":
    main()

"""Spatially-honest cross-validation and metrics.

The deployment scenario is: predict the income distribution of a city that has
NO observed income. The correct test therefore holds out WHOLE cities (or whole
countries), never individual zones. Per-zone random CV would leak the city's
income level and grossly overstate skill.

Headline metric is the per-city Spearman rank correlation between observed and
predicted index: does the emulator order rich vs poor zones correctly? That is
exactly what the downstream AC sigmoid needs.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import features as F
from . import model as M


def _spearman(a, b, w=None):
    a = pd.Series(np.asarray(a, float)).rank().values
    b = pd.Series(np.asarray(b, float)).rank().values
    if w is None:
        w = np.ones_like(a, float)
    w = np.asarray(w, float)
    ma, mb = np.average(a, weights=w), np.average(b, weights=w)
    cov = np.average((a - ma) * (b - mb), weights=w)
    sa = np.sqrt(np.average((a - ma) ** 2, weights=w))
    sb = np.sqrt(np.average((b - mb) ** 2, weights=w))
    return cov / (sa * sb) if sa > 0 and sb > 0 else np.nan


def _folds(train: pd.DataFrame, cfg: dict):
    c = cfg["columns"]
    scheme = cfg["validation"]["scheme"]
    if scheme == "leave_one_city_out":
        groups = train[c["city_id"]]
    elif scheme == "leave_one_country_out":
        groups = train[cfg["validation"]["group_column"]]
    else:  # kfold over cities
        cities = train[c["city_id"]].unique()
        k = cfg["validation"]["kfold"]
        rng = np.random.RandomState(cfg["model"]["params"].get("random_state", 42))
        assign = {city: i % k for i, city in enumerate(rng.permutation(cities))}
        groups = train[c["city_id"]].map(assign)
    for g in pd.unique(groups):
        te = groups == g
        yield str(g), ~te.values, te.values


def cross_validate(train: pd.DataFrame, cfg: dict):
    """Returns (per_fold_df, oof_predictions_df)."""
    c = cfg["columns"]
    backend = M.resolve_backend(cfg["model"]["backend"])
    X_all, feat_names = F.build_features(train, cfg)
    y_all = F.build_target(train, cfg).values
    pop = pd.to_numeric(train[c["population"]], errors="coerce").fillna(0.0).values
    weight = pop if cfg["model"]["weight_by_population"] else None

    oof = np.full(len(train), np.nan)
    for name, tr, te in _folds(train, cfg):
        if tr.sum() == 0 or te.sum() == 0:
            continue
        mdl = M.make_model(backend, cfg["model"]["params"])
        w_tr = weight[tr] if weight is not None else None
        pred = M.fit_predict(mdl, X_all.values[tr], y_all[tr], X_all.values[te], w_tr)
        oof[te] = pred

    idx_obs = F.target_to_index(y_all, cfg)
    idx_pred = F.target_to_index(oof, cfg)
    df = train[[c["city_id"], c["subdivision_id"], c["population"]]].copy()
    df["index_obs"] = idx_obs
    df["index_pred"] = idx_pred

    rows = []
    for city, g in df.groupby(c["city_id"]):
        mask = np.isfinite(g["index_pred"])
        if mask.sum() < 3:
            continue
        rows.append({
            "fold": city,
            "n_zones": int(mask.sum()),
            "spearman": _spearman(g["index_obs"][mask], g["index_pred"][mask], g[c["population"]][mask]),
            "pop_w_mae": float(np.average(np.abs(g["index_obs"][mask] - g["index_pred"][mask]),
                                          weights=g[c["population"]][mask])),
        })
    per_fold = pd.DataFrame(rows)
    return per_fold, df

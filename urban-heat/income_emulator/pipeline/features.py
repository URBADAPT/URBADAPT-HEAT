"""Target construction and within-city feature engineering.

Core idea
---------
We do NOT model absolute income (incomparable across countries, years,
currencies, income definitions). We model the WITHIN-CITY relative position:

    reference_c  = population-weighted mean income of city c   (the "1.0" point)
    target       = income_{c,i} / reference_c          (or its natural log)

Predictors are likewise expressed RELATIVE TO THEIR CITY (z-score or ratio to
the city mean), so a single pooled model learns the mapping
    "deviation of local built form / demography  ->  deviation of local income"
which transfers to cities that have no income data at all.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# Target
# ----------------------------------------------------------------------------
def city_reference(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Per-row municipal reference income value used to normalise."""
    c = cfg["columns"]
    how = cfg["target"]["reference"]
    income = pd.to_numeric(df[c["income"]], errors="coerce")
    pop = pd.to_numeric(df[c["population"]], errors="coerce").fillna(0.0)
    g = df[c["city_id"]]

    if how == "provided":
        col = cfg["target"]["reference_column"]
        return pd.to_numeric(df[col], errors="coerce")
    if how == "median":
        return g.map(income.groupby(g).median())
    if how == "mean":
        return g.map(income.groupby(g).mean())
    # default: population-weighted mean
    num = (income * pop).groupby(g).sum()
    den = pop.where(income.notna(), 0.0).groupby(g).sum()
    ref = (num / den).replace([np.inf, -np.inf], np.nan)
    return g.map(ref)


def build_target(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Relative income index (the thing we predict). 1.0 == municipal reference.

    If target.precomputed_column is set (e.g. inc_rel_to_city_median), use it
    directly instead of deriving the index from raw income. This is the path for
    the harmonized URBADAPT income file, whose relative index is already PPP- and
    definition-harmonized per city.
    """
    pre = cfg["target"].get("precomputed_column")
    if pre:
        ratio = pd.to_numeric(df[pre], errors="coerce")
    else:
        c = cfg["columns"]
        income = pd.to_numeric(df[c["income"]], errors="coerce")
        ref = city_reference(df, cfg)
        ratio = income / ref
    if cfg["target"]["log_ratio"]:
        return np.log(ratio.where(ratio > 0))
    return ratio


def target_to_index(pred: np.ndarray, cfg: dict) -> np.ndarray:
    """Invert the target transform back to the interpretable index (1.0 = mean)."""
    if cfg["target"]["log_ratio"]:
        return np.exp(pred)
    return pred


# ----------------------------------------------------------------------------
# Predictors
# ----------------------------------------------------------------------------
def _zscore_within(s: pd.Series, g: pd.Series) -> pd.Series:
    mu = s.groupby(g).transform("mean")
    sd = s.groupby(g).transform("std").replace(0, np.nan)
    return (s - mu) / sd


def _ratio_to_mean(s: pd.Series, g: pd.Series) -> pd.Series:
    mu = s.groupby(g).transform("mean").replace(0, np.nan)
    return s / mu


def build_features(df: pd.DataFrame, cfg: dict):
    """Return (X DataFrame, feature_names). No leakage: uses only predictors."""
    c = cfg["columns"]
    g = df[c["city_id"]]
    fcfg = cfg["features"]
    transform = fcfg["within_city_transform"]

    cols = {}
    for p in fcfg["predictors"]:
        s = pd.to_numeric(df[p], errors="coerce")
        if transform == "zscore_within":
            cols[f"{p}__zw"] = _zscore_within(s, g)
        elif transform == "ratio_to_mean":
            cols[f"{p}__rm"] = _ratio_to_mean(s, g)
        else:
            cols[p] = s

    # Raw city-level context (kept absolute, allows conditional effects).
    for p in fcfg.get("context", []) or []:
        if p in df.columns:
            cols[p] = pd.to_numeric(df[p], errors="coerce")

    X = pd.DataFrame(cols, index=df.index)

    # Categorical context -> one-hot.
    for p in fcfg.get("categorical_context", []) or []:
        if p in df.columns:
            dummies = pd.get_dummies(df[p].astype("category"), prefix=p, dtype=float)
            X = pd.concat([X, dummies], axis=1)

    # Median-impute remaining NaNs so tree/linear models run.
    X = X.fillna(X.median(numeric_only=True)).fillna(0.0)
    return X, list(X.columns)


def weighted_percentile_rank(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Population-weighted percentile rank in [0,1], poorer -> lower.

    Matches the 'midpoint cumulative share' convention used in URBADAPT-HEAT's
    AC downscaling (cell 38 of 05_AC_*.ipynb), so emitted p_inc is drop-in.
    """
    values = np.asarray(values, float)
    weights = np.asarray(weights, float)
    order = np.argsort(values, kind="mergesort")
    v = values[order]
    w = weights[order]
    if w.sum() <= 0:
        return np.full_like(v, 0.5, dtype=float)
    c = np.cumsum(w) - 0.5 * w
    p = c / w.sum()
    out = np.empty_like(p)
    out[order] = p
    return out


def index_to_rank(df_pred: pd.DataFrame, idx: np.ndarray, cfg: dict) -> np.ndarray:
    """Per-city percentile rank (p_inc) of the predicted index.

    Population-weighted when a usable population column exists; otherwise equal
    weights (matching the unweighted inc_pct_within_city in the harmonized file).
    """
    c = cfg["columns"]
    pop_col = c.get("population")
    if pop_col and pop_col in df_pred.columns:
        pop = pd.to_numeric(df_pred[pop_col], errors="coerce").fillna(0.0).values
        if not np.isfinite(pop).any() or pop.sum() <= 0:
            pop = np.ones(len(idx))
    else:
        pop = np.ones(len(idx))
    cities = df_pred[c["city_id"]].values
    out = np.full(len(idx), np.nan)
    for city in pd.unique(cities):
        m = cities == city
        out[m] = weighted_percentile_rank(idx[m], pop[m])
    return np.clip(out, 1e-3, 1 - 1e-3)


def renormalise_index(df_pred: pd.DataFrame, idx: np.ndarray, cfg: dict) -> np.ndarray:
    """Rescale the predicted index per city so it matches the index definition.

    mode (postprocess.renormalise_mode):
      - "pop_weighted_mean": per-city population-weighted mean -> 1.0
      - "median"           : per-city median -> 1.0 (matches inc_rel_to_city_median)
      - "none"             : leave predictions untouched
    """
    mode = cfg.get("postprocess", {}).get("renormalise_mode", "pop_weighted_mean")
    if mode == "none":
        return idx.astype(float)
    c = cfg["columns"]
    out = idx.astype(float).copy()
    cities = df_pred[c["city_id"]].values
    pop_col = c.get("population")
    if pop_col and pop_col in df_pred.columns:
        pop = pd.to_numeric(df_pred[pop_col], errors="coerce").fillna(0.0).values
    else:
        pop = np.ones(len(idx))
    for city in pd.unique(cities):
        m = cities == city
        if mode == "median":
            anchor = np.nanmedian(out[m])
        else:
            w = pop[m]
            anchor = (out[m] * w).sum() / w.sum() if w.sum() > 0 else np.nanmean(out[m])
        if anchor and np.isfinite(anchor):
            out[m] = out[m] / anchor
    return out

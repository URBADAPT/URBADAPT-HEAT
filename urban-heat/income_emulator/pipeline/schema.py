"""Input schema definition and validation.

One row per sub-city zone. The pipeline is agnostic to where the predictors
come from, but they are expected to be zonal-aggregated to each zone polygon
(see zonal.py for a helper that builds them from GHSL / GISCO 2021 grid
rasters).
"""
from __future__ import annotations
import pandas as pd


def _target_col(cfg: dict) -> str:
    """Column whose presence marks a labelled row (precomputed index or raw income)."""
    return cfg["target"].get("precomputed_column") or cfg["columns"]["income"]


def validate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Check required columns exist and types are sane. Returns df unchanged."""
    c = cfg["columns"]
    required_ids = [c["subdivision_id"], c["city_id"]]
    missing = [col for col in required_ids if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required id columns: {missing}")

    tcol = _target_col(cfg)
    if tcol not in df.columns:
        raise ValueError(
            f"Target column '{tcol}' not found. It can be blank/NaN for cities you "
            "want to predict, but the column must exist."
        )

    preds = cfg["features"]["predictors"]
    missing_pred = [p for p in preds if p not in df.columns]
    if missing_pred:
        raise ValueError(
            f"Missing predictor columns: {missing_pred}. "
            "Build them with covariates.py or remove them from config.features.predictors."
        )

    sid = c["subdivision_id"]
    if df[sid].duplicated().any():
        dups = df.loc[df[sid].duplicated(), sid].unique()[:5]
        raise ValueError(f"Duplicate subdivision ids, e.g. {list(dups)}")

    pop_col = c.get("population")
    if pop_col and pop_col in df.columns:
        pop = pd.to_numeric(df[pop_col], errors="coerce")
        if (pop.dropna() < 0).any():
            raise ValueError("Population contains negative values.")

    return df


def split_train_predict(df: pd.DataFrame, cfg: dict):
    """Rows with a non-null income belong to TRAIN cities; the rest are predicted.

    A city is a TRAIN city only if it has at least 2 labelled zones (need
    within-city variation to define a meaningful relative index).
    """
    c = cfg["columns"]
    income = pd.to_numeric(df[_target_col(cfg)], errors="coerce")
    labelled = income.notna()
    cnt = df.loc[labelled].groupby(df[c["city_id"]]).size()
    train_cities = set(cnt[cnt >= 2].index)
    is_train_city = df[c["city_id"]].isin(train_cities)
    train = df[is_train_city & labelled].copy()
    predict = df[~is_train_city | ~labelled].copy()
    return train, predict, sorted(train_cities)

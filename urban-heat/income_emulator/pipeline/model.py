"""Pluggable regression backend.

Preference order for backend=="auto":
    lightgbm  ->  sklearn GradientBoosting  ->  numpy ridge (always available)

The numpy ridge fallback exists so the pipeline runs and self-tests anywhere
(no third-party ML libs). For real work install lightgbm or scikit-learn and
set model.backend accordingly; gradient-boosted trees capture the non-linear,
interacting relationships between built form / demography and income far better
than ridge.
"""
from __future__ import annotations
import numpy as np


def _try_import(name):
    try:
        return __import__(name)
    except Exception:
        return None


def resolve_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if _try_import("lightgbm") is not None:
        return "lightgbm"
    if _try_import("sklearn") is not None:
        return "sklearn_gbr"
    return "ridge_numpy"


# ----------------------------------------------------------------------------
class RidgeNumpy:
    """Closed-form ridge regression with standardisation. Dependency-free."""

    def __init__(self, alpha: float = 1.0, **_):
        self.alpha = alpha

    def fit(self, X, y, sample_weight=None):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        self.mu_ = X.mean(0)
        self.sd_ = X.std(0)
        self.sd_[self.sd_ == 0] = 1.0
        Xs = (X - self.mu_) / self.sd_
        n, p = Xs.shape
        Xb = np.hstack([np.ones((n, 1)), Xs])
        w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, float)
        W = Xb * w[:, None]
        A = Xb.T @ W
        reg = self.alpha * np.eye(p + 1)
        reg[0, 0] = 0.0  # do not penalise intercept
        self.coef_ = np.linalg.solve(A + reg, Xb.T @ (w * y))
        return self

    def predict(self, X):
        X = np.asarray(X, float)
        Xs = (X - self.mu_) / self.sd_
        Xb = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
        return Xb @ self.coef_


# ----------------------------------------------------------------------------
def make_model(backend: str, params: dict):
    """Return an estimator exposing fit(X, y, sample_weight) / predict(X)."""
    p = dict(params)

    if backend == "lightgbm":
        import lightgbm as lgb
        return lgb.LGBMRegressor(
            n_estimators=p.get("n_estimators", 600),
            learning_rate=p.get("learning_rate", 0.03),
            max_depth=p.get("max_depth", -1),
            num_leaves=p.get("num_leaves", 31),
            min_child_samples=p.get("min_child_samples", 20),
            subsample=p.get("subsample", 0.8),
            colsample_bytree=p.get("colsample_bytree", 0.8),
            random_state=p.get("random_state", 42),
            n_jobs=-1,
            verbose=-1,
        )

    if backend in ("sklearn_gbr", "sklearn_rf"):
        if backend == "sklearn_gbr":
            from sklearn.ensemble import GradientBoostingRegressor
            return GradientBoostingRegressor(
                n_estimators=p.get("n_estimators", 600),
                learning_rate=p.get("learning_rate", 0.03),
                max_depth=p.get("max_depth", 3) if p.get("max_depth", -1) > 0 else 3,
                subsample=p.get("subsample", 0.8),
                random_state=p.get("random_state", 42),
            )
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(
            n_estimators=p.get("n_estimators", 600),
            min_samples_leaf=p.get("min_child_samples", 20),
            max_features=p.get("colsample_bytree", 0.8),
            random_state=p.get("random_state", 42),
            n_jobs=-1,
        )

    if backend == "ridge_numpy":
        return RidgeNumpy(alpha=p.get("alpha", 1.0))

    raise ValueError(f"Unknown backend: {backend}")


def fit_predict(model, X_tr, y_tr, X_te, w_tr=None):
    try:
        model.fit(X_tr, y_tr, sample_weight=w_tr)
    except TypeError:
        model.fit(X_tr, y_tr)
    return model.predict(X_te)

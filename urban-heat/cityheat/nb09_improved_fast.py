# this is the main uncertainty quantification script

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(_DEFAULT_RUNTIME_ROOT / "outputs" / ".mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(_DEFAULT_RUNTIME_ROOT / "outputs" / ".cache"))

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio as rio
import xarray as xr
import yaml
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from SALib.analyze import pawn
from cityheat.nbsetup import find_repo_root
from cityheat.vulnerability_layer import (
    get_vuln_config,
    load_vulnerability,
    compute_svi,
    _load_drmkc_component_series,
    _load_gvi_series,
    _phi_for_year,
    _project_component_mean,
    _project_absolute_component_grid,
    _project_thermal_component,
    _load_population_array,
)
from climada.engine import ImpactCalc
from climada.entity import Exposures, ImpactFunc, ImpactFuncSet
from climada.hazard import Centroids, Hazard
from pyproj import Transformer
from scipy import ndimage as ndi, sparse
from scipy.stats import qmc


AGE_ORDER = ["<15", "15-64", "65+"]
AGE_TO_ID = {"<15": 1, "15-64": 2, "65+": 3}
IF_FAMILIES = ["burke_polynomial", "burke_powerlaw", "masselot", "masselot_tail"]
TREF_OPTIONS = [18.0, 20.0, 22.0, 24.0, 26.0]
TREF_BASE = 20.0
DAILY_QUANTILE_PCTS = [50, 80, 90, 95]  # percentiles of the DAILY heat-death distribution (NOT annual return periods)
HORIZON_YEARS = 25
DISCOUNT_RATE_DEFAULT = 0.03
SEED_DEFAULT = 42
OUTPUT_SCHEMA_VERSION = "3.0-explicit-policy-trajectories"
OUTPUT_SCHEMA_TAG = "v3"
INPUT_FULL_HASH_LIMIT_BYTES = 32 * 1024 * 1024
INPUT_SAMPLE_HASH_WINDOWS = 3
INPUT_SAMPLE_HASH_WINDOW_BYTES = 64 * 1024
BRANCH_NAMES = (
    "reference",
    "ac_policy_gross",
    "ac_policy_net",
    "ac_policy_net_with_tree_feedback",
    "tree_policy",
    "ews_policy",
    "ac_tree_policy_gross",
    "ac_tree_policy_net",
)


def _resolve_root() -> Path:
    return find_repo_root(Path(__file__).resolve())


def _ensure_runtime_dirs(root: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(root / "outputs" / ".mpl"))
    os.environ.setdefault("XDG_CACHE_HOME", str(root / "outputs" / ".cache"))
    (root / "outputs" / ".mpl").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / ".cache").mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def _safe_run_id(value: str) -> str:
    """Validate a user-visible campaign identifier before using it as a path."""
    run_id = str(value).strip()
    if not run_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise ValueError(
            "NB09_CAMPAIGN_ID must be 1--128 characters and contain only "
            "letters, digits, '.', '_' or '-'."
        )
    return run_id


def _headline_uncertainty_dir(tab_dir: Path) -> Path:
    """Resolve the isolated production directory, with a legacy fallback."""
    campaign_id = os.environ.get("NB09_CAMPAIGN_ID", "").strip()
    if campaign_id:
        return tab_dir / "uncertainty_runs" / _safe_run_id(campaign_id)
    return tab_dir / "uncertainty_improved_fast"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text)
    os.replace(temporary, path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(_json_ready(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _atomic_write_csv(path: Path, frame: pd.DataFrame, *, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    frame.to_csv(temporary, index=index)
    os.replace(temporary, path)


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _input_file_fingerprint(path: Path) -> dict[str, Any]:
    """Return a reproducible input fingerprint without rereading huge rasters.

    Small files receive a full SHA-256. For inputs above 32 MiB, the digest
    covers the file size and three evenly spaced 64 KiB windows (start, middle,
    and end). Size and nanosecond
    modification time are also recorded. This is a scientific change detector,
    not an adversarial integrity primitive; the sampling rule is declared in
    the manifest so the provenance claim remains explicit.
    """
    stat = path.stat()
    size = int(stat.st_size)
    base = {
        "size_bytes": size,
        "mtime_ns": int(stat.st_mtime_ns),
    }
    if size <= INPUT_FULL_HASH_LIMIT_BYTES:
        return {
            **base,
            "digest_algorithm": "sha256-full-v1",
            "digest": _sha256_file(path),
        }

    window = min(INPUT_SAMPLE_HASH_WINDOW_BYTES, size)
    max_offset = max(size - window, 0)
    offsets = np.linspace(0, max_offset, num=INPUT_SAMPLE_HASH_WINDOWS, dtype=np.int64)
    offsets = np.unique(offsets).astype(int).tolist()
    digest = hashlib.sha256()
    digest.update(b"urbadapt-input-sampled-v1\0")
    digest.update(str(size).encode("ascii"))
    with open(path, "rb") as handle:
        for offset in offsets:
            handle.seek(int(offset))
            block = handle.read(window)
            digest.update(int(offset).to_bytes(8, byteorder="big", signed=False))
            digest.update(len(block).to_bytes(8, byteorder="big", signed=False))
            digest.update(block)
    return {
        **base,
        "digest_algorithm": f"sha256-sampled-{len(offsets)}x{int(window)}B-v1",
        "digest": digest.hexdigest(),
        "sample_offsets_bytes": offsets,
        "sample_window_bytes": int(window),
    }


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_ready(value: Any) -> Any:
    """Convert NumPy/Pandas containers into strict, checkpoint-safe JSON values."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_json_ready(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if np.isnan(number):
            return {"__nonfinite_float__": "nan"}
        if np.isposinf(number):
            return {"__nonfinite_float__": "inf"}
        if np.isneginf(number):
            return {"__nonfinite_float__": "-inf"}
        return number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    if pd.isna(value):
        return None
    return value


def _json_restore(value: Any) -> Any:
    """Restore strict-JSON non-finite sentinels used by sample checkpoints."""
    if isinstance(value, dict):
        if set(value) == {"__nonfinite_float__"}:
            return {
                "nan": np.nan,
                "inf": np.inf,
                "-inf": -np.inf,
            }[str(value["__nonfinite_float__"])]
        return {key: _json_restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_restore(item) for item in value]
    return value


def _mode_tag(mode: str) -> str:
    return str(mode).strip().lower().replace("_", "-")


def _safe_quantile_threshold(values: np.ndarray, target_days: int) -> float:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return np.inf
    target_days = int(np.clip(target_days, 1, vals.size))
    order = np.sort(vals)[::-1]
    return float(order[target_days - 1])


def _season_mask_by_md(dates: np.ndarray, start_md: str, end_md: str) -> np.ndarray:
    dates_dt = pd.to_datetime(dates)
    years = pd.Index(dates_dt).year.astype(str)
    start = pd.to_datetime(years + "-" + start_md)
    end = pd.to_datetime(years + "-" + end_md)
    return np.asarray((dates_dt >= start) & (dates_dt <= end), dtype=bool)


def _ecdf(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(arr, dtype=float))
    if x.size == 0:
        return x, x
    y = np.arange(1, x.size + 1, dtype=float) / x.size
    return x, y


def _summarize_sensitivity(
    df: pd.DataFrame | None,
    target: str,
    *,
    si: str,
    exclude_params: set[str] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty or "param" not in df.columns or target not in df.columns:
        return pd.DataFrame(columns=["param", target])

    work = df.copy()
    if "si" in work.columns:
        mask = work["si"].astype(str).str.lower() == si.lower()
        if mask.any():
            work = work.loc[mask].copy()
    work["param"] = work["param"].astype(str)
    work[target] = pd.to_numeric(work[target], errors="coerce")
    work = work.dropna(subset=["param", target])
    if exclude_params:
        work = work.loc[~work["param"].isin(exclude_params)].copy()
    if work.empty:
        return pd.DataFrame(columns=["param", target])
    return (
        work.groupby("param", as_index=False)[target]
        .mean()
        .sort_values(target, ascending=False)
        .reset_index(drop=True)
    )


def _bool_options_with_baseline(options: list[Any], baseline: bool) -> list[bool]:
    out: list[bool] = []
    for x in [baseline] + list(options):
        b = bool(x)
        if b not in out:
            out.append(b)
    return out


def _interp_1d_years(anchor_years: np.ndarray, anchor_values: np.ndarray, years: np.ndarray) -> np.ndarray:
    return np.interp(years.astype(float), anchor_years.astype(float), anchor_values.astype(float))


def _scale_pattern_to_mean(pattern: np.ndarray, target_mean: float, upper: float = 0.98) -> np.ndarray:
    pattern = np.asarray(pattern, dtype=np.float32)
    target_mean = float(target_mean)
    if target_mean <= 0.0:
        return np.zeros_like(pattern, dtype=np.float32)
    if not np.any(pattern > 0):
        return np.full_like(pattern, target_mean, dtype=np.float32)
    lo = 0.0
    hi = max(target_mean * 3.0, upper / max(float(pattern.max()), 1e-6) * 2.0)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        arr = np.clip(pattern * mid, 0.0, upper)
        mean_val = float(arr.mean())
        if mean_val < target_mean:
            lo = mid
        else:
            hi = mid
    return np.clip(pattern * hi, 0.0, upper).astype(np.float32)


def _scale_pattern_to_mean_masked(
    pattern: np.ndarray,
    active_mask: np.ndarray,
    target_mean: float,
    upper: float = 0.98,
) -> np.ndarray:
    pattern = np.asarray(pattern, dtype=np.float32)
    active_mask = np.asarray(active_mask, dtype=bool)
    out = np.zeros_like(pattern, dtype=np.float32)
    if not np.any(active_mask):
        return out
    out[active_mask] = _scale_pattern_to_mean(pattern[active_mask], target_mean, upper=upper)
    return out


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not np.any(valid):
        return 0.0
    return float(np.average(values[valid], weights=weights[valid]))


def _scale_pattern_to_weighted_mean(
    pattern: np.ndarray,
    weights: np.ndarray,
    target_mean: float,
    upper: float | None = None,
) -> np.ndarray:
    pattern = np.asarray(pattern, dtype=np.float32)
    weights = np.asarray(weights, dtype=float)
    target_mean = float(target_mean)
    out = np.zeros_like(pattern, dtype=np.float32)

    active = np.isfinite(pattern) & np.isfinite(weights) & (weights > 0)
    if not np.any(active) or target_mean <= 0.0:
        return out

    active_pattern = pattern[active].astype(float)
    active_weights = weights[active].astype(float)
    if not np.any(active_pattern > 0):
        out[active] = target_mean
        if upper is not None:
            out = np.clip(out, 0.0, float(upper))
        return out.astype(np.float32)

    lo = 0.0
    hi = max(target_mean / max(_weighted_mean(active_pattern, active_weights), 1e-6) * 2.0, 2.0)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        arr = active_pattern * mid
        if upper is not None:
            arr = np.clip(arr, 0.0, float(upper))
        mean_val = _weighted_mean(arr, active_weights)
        if mean_val < target_mean:
            lo = mid
        else:
            hi = mid

    scaled = active_pattern * hi
    if upper is not None:
        scaled = np.clip(scaled, 0.0, float(upper))
    out[active] = scaled.astype(np.float32)
    return out.astype(np.float32)


def _pawn_table(
    problem: dict[str, Any],
    x: np.ndarray,
    metric_map: dict[str, np.ndarray],
    *,
    log_metrics: set[str] | None = None,
) -> pd.DataFrame:
    metric_tables: list[pd.DataFrame] = []
    analyzed_metrics: list[str] = []
    x_arr = np.asarray(x, dtype=float)
    log_metrics = set(log_metrics or ())

    for metric, y in metric_map.items():
        y_arr = np.asarray(y, dtype=float)
        valid = np.isfinite(y_arr)
        if metric in log_metrics:
            # Ratios are strictly positive; keep finite positive values only.
            valid &= y_arr > 0.0
        if not np.any(valid):
            continue

        y_use = y_arr[valid]
        if metric in log_metrics:
            y_use = np.log10(y_use)

        try:
            res = pawn.analyze(problem, x_arr[valid, :], y_use, S=10, seed=SEED_DEFAULT)
        except Exception:
            continue

        analyzed_metrics.append(metric)
        rows: list[dict[str, Any]] = []
        for idx, param in enumerate(res["names"]):
            for si in ["minimum", "mean", "median", "maximum", "CV"]:
                rows.append({"si": si, "param": param, metric: float(res[si][idx])})
        metric_tables.append(pd.DataFrame(rows))

    if not metric_tables or not analyzed_metrics:
        return pd.DataFrame()

    out = None
    for sub in metric_tables:
        out = sub if out is None else out.merge(sub, on=["si", "param"], how="outer")
    out["param2"] = np.nan
    cols = ["si", "param", "param2", *analyzed_metrics]
    return out[cols]


def _daily_quantiles(daily_impacts: np.ndarray, pcts: list[int]) -> dict[str, float]:
    """Percentiles of the DAILY heat-death distribution (NOT annual return levels).

    Keys are ``daily_p{pct}`` (e.g. daily_p50/p80/p90/p95): the deaths on the day at
    that percentile of the year's daily-death series. The earlier form divided by
    ``1 - 1/rp`` and mislabeled these as 2/5/10/20-year return periods.
    """
    vals = np.asarray(daily_impacts, dtype=float)
    out: dict[str, float] = {}
    for p in pcts:
        out[f"daily_p{int(p)}"] = float(np.percentile(vals, p)) if vals.size else np.nan
    return out


def _translate_dlst_to_dt2m_quadratic(
    t2m: np.ndarray,
    dlst: np.ndarray,
    lcz: np.ndarray,
    gvi_0_1: np.ndarray,
    scope: np.ndarray,
    emulator: dict[str, Any],
) -> np.ndarray:
    """Translate an LST perturbation to T2M exactly as canonical Notebook 07.

    The quadratic emulator is inverted at the *daily, cell-specific* baseline
    temperature.  Invalid/non-positive local slopes and implausible roots use
    Notebook 07's median-positive-slope fallback.  Values outside the physical
    vegetation scope are zero.
    """
    t2m = np.asarray(t2m, dtype=np.float32)
    dlst = np.asarray(dlst, dtype=np.float32)
    lcz = np.asarray(lcz)
    gvi_0_1 = np.asarray(gvi_0_1, dtype=np.float32)
    scope = np.asarray(scope, dtype=bool)
    if not (t2m.shape == dlst.shape == lcz.shape == gvi_0_1.shape == scope.shape):
        raise ValueError("T2M, dLST, LCZ, GVI, and scope arrays must have identical shapes.")

    lcz_int = np.nan_to_num(lcz, nan=-999).astype(int)
    beta_lcz = np.zeros(t2m.shape, dtype=np.float32)
    reference_lcz = int(emulator["reference_lcz"])
    beta_lcz[lcz_int == reference_lcz] = 0.0
    for key, value in (emulator.get("beta_t2m_c_lcz", {}) or {}).items():
        beta_lcz[lcz_int == int(key)] = float(value)

    beta2 = float(emulator["beta_t2m_c2"])
    centered = t2m - float(emulator["t2m_center"])
    slope = (
        float(emulator["beta_t2m_c"])
        + 2.0 * beta2 * centered
        + float(emulator.get("beta_t2m_c_out_b", 0.0)) * gvi_0_1
        + beta_lcz
    ).astype(np.float32)

    linear = np.full(dlst.shape, np.nan, dtype=np.float32)
    good_linear = np.isfinite(slope) & (np.abs(slope) > 1e-8)
    linear[good_linear] = dlst[good_linear] / slope[good_linear]
    if abs(beta2) < 1e-12:
        dt2m = linear.copy()
    else:
        discriminant = np.maximum(slope**2 + 4.0 * beta2 * dlst, 0.0)
        sqrt_discriminant = np.sqrt(discriminant).astype(np.float32)
        root1 = (-slope + sqrt_discriminant) / (2.0 * beta2)
        root2 = (-slope - sqrt_discriminant) / (2.0 * beta2)
        dt2m = np.where(np.abs(root1 - linear) <= np.abs(root2 - linear), root1, root2).astype(np.float32)

    bad = (~np.isfinite(dt2m)) | (~np.isfinite(slope)) | (slope <= 0)
    bad |= (dlst < 0) & (dt2m > 1e-6)
    bad |= (dlst > 0) & (dt2m < -1e-6)
    bad |= np.abs(dt2m) > 10
    positive_slopes = slope[np.isfinite(slope) & (slope > 0)]
    fallback = float(np.nanmedian(positive_slopes)) if positive_slopes.size else 1.5
    if not np.isfinite(fallback) or fallback <= 0:
        fallback = 1.5
    dt2m[bad] = dlst[bad] / fallback
    return np.where(scope, dt2m, 0.0).astype(np.float32)


def _safe_ratio(num: float, den: float) -> float:
    return float(num) / float(den) if float(den) > 0 else np.inf


def _pv_capex_with_replacements(
    new_users_t: np.ndarray,
    capex_per_user: float,
    lifetime_years: int,
    discount_rate: float,
) -> float:
    """Present value of cohort-based AC CAPEX with replacement cycles."""
    stream = _capex_replacement_stream(new_users_t, capex_per_user, lifetime_years)
    discount = (1.0 + float(discount_rate)) ** np.arange(1, stream.size + 1, dtype=float)
    return float(np.sum(stream / discount))


def _capex_replacement_stream(
    new_users_t: np.ndarray,
    capex_per_user: float,
    lifetime_years: int,
) -> np.ndarray:
    """Nominal annual AC CAPEX stream, including cohort replacements."""
    new_users_t = np.asarray(new_users_t, dtype=float)
    horizon = int(new_users_t.size)
    life = max(int(lifetime_years), 1)
    stream = np.zeros(horizon, dtype=float)
    for start_idx, cohort in enumerate(new_users_t):
        cohort = float(cohort)
        if cohort <= 0:
            continue
        pay_idx = int(start_idx)
        while pay_idx < horizon:
            stream[pay_idx] += cohort * float(capex_per_user)
            pay_idx += life
    return stream


def _cohort_rollout_maturity_factor(
    years: int,
    ramp_years: int,
    *,
    start_age_years: int = 0,
    lifetime_years: int | None = None,
) -> np.ndarray:
    """Cohort-based maturity factor used for tree O&M/cooling rollout."""
    years = int(years)
    if years <= 0:
        return np.zeros(0, dtype=float)
    plant_share = np.ones(years, dtype=float) / float(years)
    max_age = years if lifetime_years is None else min(years, max(int(lifetime_years), 1))
    ages = np.arange(max_age + 1, dtype=float)
    maturity = np.minimum((ages + float(start_age_years)) / max(float(ramp_years), 1.0), 1.0)
    maturity[0] = 0.0
    return np.convolve(plant_share, maturity)[:years]


def _npv_capex_linear(
    delta_index_total: float,
    years: int,
    discount_rate: float,
    capex_per_index_pt: float,
) -> float:
    """Present value of tree CAPEX assuming linear annual rollout."""
    stream = _tree_capex_linear_stream(delta_index_total, years, capex_per_index_pt)
    discount = (1.0 + float(discount_rate)) ** np.arange(1, stream.size + 1, dtype=float)
    return float(np.sum(stream / discount))


def _tree_capex_linear_stream(
    delta_index_total: float,
    years: int,
    capex_per_index_pt: float,
) -> np.ndarray:
    """Nominal annual tree CAPEX stream for a linear rollout."""
    years = int(years)
    if years <= 0:
        return np.zeros(0, dtype=float)
    annual_increment = float(delta_index_total) / float(years)
    return np.full(years, float(capex_per_index_pt) * annual_increment, dtype=float)


def _npv_om_cohorts_scaled(
    delta_index_total: float,
    years: int,
    discount_rate: float,
    om_per_index_per_year: float,
    ramp_years: int,
    lifetime_years: int,
    start_age_years: int,
) -> tuple[float, np.ndarray]:
    """Present value of tree O&M with cohort-based maturity scaling."""
    factor = _cohort_rollout_maturity_factor(
        years,
        ramp_years,
        start_age_years=start_age_years,
        lifetime_years=lifetime_years,
    )
    om_stream = float(om_per_index_per_year) * float(delta_index_total) * factor
    discount = (1.0 + float(discount_rate)) ** np.arange(1, int(years) + 1, dtype=float)  # end-of-year (t=1..T) to match NB08
    pv = float(np.sum(om_stream / discount))
    return pv, om_stream


@dataclass
class ParamSpec:
    name: str
    kind: str
    options: list[Any] | None = None
    low: float | None = None
    high: float | None = None


class NB09ImprovedFast:
    def __init__(self, slug: str):
        self.root = _resolve_root()
        _ensure_runtime_dirs(self.root)

        self.slug = str(slug).strip().lower()
        self.cfg_path = self.root / "configs" / f"{self.slug}.yml"
        if not self.cfg_path.exists():
            raise FileNotFoundError(f"Missing config: {self.cfg_path}")

        with open(self.cfg_path, "r") as f:
            self.cfg = yaml.safe_load(f)

        self.city = self.cfg.get("city_name", self.slug.title())
        base_dir_cfg = self.cfg.get("base_dir")
        if base_dir_cfg:
            self.base = (self.root / str(base_dir_cfg)).resolve()
        else:
            self.base = self.root / "data" / self.slug
        # Backward-compatible alias used by some helper loaders.
        self.base_dir = self.base
        self.out = self.root / "outputs" / self.slug
        self.int_dir = self.out / "interim"
        self.tab_dir = self.out / "tables"
        self.unc_dir = _headline_uncertainty_dir(self.tab_dir)
        self.unc_dir.mkdir(parents=True, exist_ok=True)
        self._headline_unc_dir = self.unc_dir

        self.exp_cache: dict[str, Exposures] = {}
        self.exp_age_cache: dict[tuple[str, str], Exposures] = {}
        self.if_block_cache: dict[tuple[str, int], dict[str, Any]] = {}

        self._configure_hazard_track()
        self._load_core_artifacts()
        self._load_hazard_scaffold()
        self._load_climate_inputs()
        self._load_exposure_inputs()
        self._load_ac_inputs()
        self._load_ews_inputs()
        self._load_tree_inputs()
        self._load_vulnerability_baseline()
        self._build_param_specs()

    def P(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.base / p)

    def _find_first_existing(self, candidates: list[Path]) -> Path:
        for path in candidates:
            if path.exists():
                return path
        raise FileNotFoundError("Could not find any candidate file:\n" + "\n".join(str(p) for p in candidates))

    def _configure_hazard_track(self) -> None:
        ext_cfg = self.cfg.get("extreme_hazard", {}) or {}
        std_cfg = ext_cfg.get("standard_track", {}) or {}
        evt_cfg = ext_cfg.get("event_track", {}) or {}

        self.extreme_track_config_enabled = bool(ext_cfg.get("enabled", False)) and bool(
            ext_cfg.get("run_extreme_track", False)
        )
        track_default = "extreme" if self.extreme_track_config_enabled else "standard"
        track_req = str(os.environ.get("HAZARD_TRACK", track_default)).strip().lower()
        use_extreme = track_req in {"extreme", "event", "track_b", "heatwave"}

        if use_extreme and (not self.extreme_track_config_enabled):
            use_extreme = False

        if (not use_extreme) and self.extreme_track_config_enabled and (not bool(std_cfg.get("run_policies", True))):
            raise RuntimeError(
                f"Standard policy track is disabled for {self.slug} "
                "(extreme_hazard.standard_track.run_policies=false). "
                "Set HAZARD_TRACK=extreme (or track_b) for NB09 improved."
            )

        self.use_extreme_track = bool(use_extreme)
        self.hazard_track = "extreme" if self.use_extreme_track else "standard"
        self.track_suffix = "_extreme" if self.use_extreme_track else ""

        season_cfg = evt_cfg.get("season", {}) or {}
        self.extreme_season_start_md = str(season_cfg.get("start_md", "05-15"))
        self.extreme_season_end_md = str(season_cfg.get("end_md", "09-30"))
        self.extreme_min_duration_days = max(1, int(evt_cfg.get("min_duration_default", 3)))
        self.extreme_threshold_pct = float(evt_cfg.get("threshold_percentile_default", 95.0))
        self.extreme_threshold_options = sorted(
            set(float(x) for x in (evt_cfg.get("threshold_percentile_options") or [self.extreme_threshold_pct]))
        )
        self.extreme_min_duration_options = sorted(
            set(max(1, int(x)) for x in (evt_cfg.get("min_duration_options") or [self.extreme_min_duration_days]))
        )
        self.extreme_tstar_c = np.nan
        self.extreme_meta: dict[str, Any] = {}

    def _load_extreme_track_meta(self) -> None:
        if not self.use_extreme_track:
            return

        meta_candidates = [
            self.int_dir / f"hazard_extreme_meta_{self.slug}.json",
            self.out / f"hazard_extreme_meta_{self.slug}.json",
        ]
        meta_path = self._find_first_existing(meta_candidates)
        self.extreme_meta = _load_json(meta_path)

        t_star = self.extreme_meta.get("threshold_degC", np.nan)
        t_star = float(t_star) if t_star is not None else np.nan
        if not np.isfinite(t_star):
            raise ValueError(
                f"Extreme track requested for {self.slug}, but threshold_degC is missing in {meta_path}."
            )
        self.extreme_tstar_c = t_star
        self.extreme_threshold_pct = float(self.extreme_meta.get("threshold_percentile", self.extreme_threshold_pct))
        self.extreme_min_duration_days = max(
            1, int(self.extreme_meta.get("min_duration_days", self.extreme_min_duration_days))
        )
        self.extreme_season_start_md = str(
            self.extreme_meta.get("season_start_md", self.extreme_season_start_md)
        )
        self.extreme_season_end_md = str(
            self.extreme_meta.get("season_end_md", self.extreme_season_end_md)
        )

    def _load_core_artifacts(self) -> None:
        self.template_tif = self.int_dir / "template_ref.tif"
        self.city_mask_npz = self.int_dir / "city_mask.npz"
        self.exp_manifest_path = self.int_dir / "exposure_manifest.json"
        self.if_jsons = {
            "burke_polynomial": self.int_dir / f"if_curves_by_year_{self.slug}.json",
            "burke_powerlaw": self.int_dir / f"if_curves_by_year_{self.slug}_powerlaw.json",
        }
        masselot_path = self.int_dir / f"if_curves_by_year_{self.slug}_masselot.json"
        if masselot_path.exists():
            self.if_jsons["masselot"] = masselot_path
        masselot_tail_path = self.int_dir / f"if_curves_by_year_{self.slug}_masselot_tail.json"
        if masselot_tail_path.exists():
            self.if_jsons["masselot_tail"] = masselot_tail_path
        self.available_if_families = [f for f in IF_FAMILIES if f in self.if_jsons]
        if not self.available_if_families:
            raise FileNotFoundError(
                "No impact-function JSONs found in "
                f"{self.int_dir} for {', '.join(IF_FAMILIES)}"
            )
        if self.use_extreme_track:
            self.haz_events_csv = self._find_first_existing(
                [
                    self.int_dir / f"hazard_T2M_daily_events_{self.slug}_extreme.csv",
                    self.out / f"hazard_T2M_daily_events_{self.slug}_extreme.csv",
                ]
            )
            self._load_extreme_track_meta()
        else:
            self.haz_events_csv = self.int_dir / f"hazard_T2M_daily_events_{self.slug}.csv"
        req = [self.template_tif, self.city_mask_npz, self.exp_manifest_path, self.haz_events_csv, *self.if_jsons.values()]
        for path in req:
            if not path.exists():
                raise FileNotFoundError(f"Missing required input: {path}")

    def _load_hazard_scaffold(self) -> None:
        with rio.open(self.template_tif) as src:
            self.ref_transform = src.transform
            self.ref_crs = src.crs
            self.hgt = src.height
            self.wdt = src.width

        self.city_mask = np.load(self.city_mask_npz)["city_mask"].astype(bool)
        self.mask_vec = self.city_mask.ravel().astype(np.float32)
        self.city_cols = np.flatnonzero(self.city_mask.ravel())
        self.n_city = int(self.city_cols.size)
        self.row_cols: np.ndarray | None = None
        self.row_is_city: np.ndarray | None = None

        rows, cols = np.indices((self.hgt, self.wdt))
        xs_m, ys_m = rio.transform.xy(self.ref_transform, rows, cols, offset="center")
        x_flat = np.asarray(xs_m, dtype=float).ravel()
        y_flat = np.asarray(ys_m, dtype=float).ravel()
        to_wgs84 = Transformer.from_crs(self.ref_crs, "EPSG:4326", always_xy=True)
        lon, lat = to_wgs84.transform(x_flat, y_flat)
        self.centroids = Centroids(lat=np.asarray(lat, float), lon=np.asarray(lon, float))
        self.hazard_template = Hazard("T2M")
        self.hazard_template.haz_type = "T2M"
        self.hazard_template.centroids = self.centroids

        ev = pd.read_csv(self.haz_events_csv)
        if "date" in ev.columns:
            ev["date"] = pd.to_datetime(ev["date"])
        elif {"year", "month", "day"}.issubset(ev.columns):
            ev["date"] = pd.to_datetime(ev[["year", "month", "day"]])
        else:
            raise ValueError(f"Cannot parse dates from {self.haz_events_csv}")

        ev = ev.sort_values("date").reset_index(drop=True)
        self.years = sorted(ev["date"].dt.year.unique().tolist())
        self.year_rows = {y: ev.index[ev["date"].dt.year == y].to_numpy(dtype=int) for y in self.years}
        self.months_by_year = {
            y: ev.loc[self.year_rows[y], "date"].dt.month.to_numpy(dtype=int) for y in self.years
        }
        self.dates_by_year = {
            y: ev.loc[self.year_rows[y], "date"].to_numpy(dtype="datetime64[ns]") for y in self.years
        }

        self.clim_cfg = self.cfg.get("climate", {})
        modes_cfg = list(
            self.clim_cfg.get(
                "t2m_baseline_mode_options",
                ["climatology_mean", "pixelwise_doy_max", "domain_peak_day", "warmest_summer"],
            )
        )
        self.ref_scen = "CurPol"
        self.ref_band = "central"
        haz_dir = self.out / "hazard"

        def tagged_haz_path(year: int, mode: str, scen: str = self.ref_scen, band: str = self.ref_band) -> Path:
            stem = (
                f"T2M_daily_mean_{year}_FUA_degC__bm-{_mode_tag(mode)}__sc-{str(scen).lower()}"
                f"__pb-{str(band).lower()}.nc"
            )
            return haz_dir / stem

        self.tagged_haz_path = tagged_haz_path
        self.baseline_modes = [m for m in modes_cfg if all(tagged_haz_path(y, m).exists() for y in self.years)]
        if not self.baseline_modes:
            raise FileNotFoundError("No fully-available tagged baseline mode found for all modeled years.")
        self.ref_mode = "climatology_mean" if "climatology_mean" in self.baseline_modes else self.baseline_modes[0]
        self._used_fixed_city_pattern = False

        self.base_matrix_by_year: dict[int, sparse.csr_matrix] = {}
        self.ref_citymean_by_year: dict[int, np.ndarray] = {}
        self.mode_day_anom: dict[tuple[str, int], np.ndarray] = {}

        for y in self.years:
            arr = self._load_nc_time_yx(tagged_haz_path(y, self.ref_mode))
            if arr.shape[0] != len(self.year_rows[y]):
                raise ValueError(f"Day mismatch for {y}: tagged NC={arr.shape[0]}, events={len(self.year_rows[y])}")
            citymean = np.nanmean(arr[:, self.city_mask], axis=1).astype(np.float32)
            self.ref_citymean_by_year[y] = citymean
            mat = np.nan_to_num(arr, nan=0.0).reshape(arr.shape[0], -1).astype(np.float32)
            csr = sparse.csr_matrix(mat)
            try:
                self._assert_city_pattern(csr)
            except ValueError:
                # Some cities' daily hazard NetCDFs have a valid-cell (non-zero) pattern that
                # varies across days (NaN/0 boundary cells appear/disappear day-to-day). Fall
                # back to a FIXED city-mask column set for ANY such city (was Copenhagen-only).
                # This only runs for cities that fail the stability check, so cities that pass
                # are unaffected; filled cells carry 0 degC -> ~0 heat deaths (as for Copenhagen).
                if not self._used_fixed_city_pattern:
                    self.row_cols = np.where(self.city_mask.ravel())[0].astype(np.int32)
                    self.row_is_city = np.ones(len(self.row_cols), dtype=bool)
                    self._used_fixed_city_pattern = True
                csr = self._csr_with_fixed_row_cols(mat, self.row_cols)
            self.base_matrix_by_year[y] = csr

        for mode in self.baseline_modes:
            for y in self.years:
                arr = self._load_nc_time_yx(tagged_haz_path(y, mode))
                cm = np.nanmean(arr[:, self.city_mask], axis=1).astype(np.float32)
                self.mode_day_anom[(mode, y)] = cm - self.ref_citymean_by_year[y]

    def _assert_city_pattern(self, mat: sparse.csr_matrix) -> None:
        if self.row_cols is None:
            self.row_cols = mat.indices[mat.indptr[0] : mat.indptr[1]].copy()
            self.row_is_city = self.city_mask.ravel()[self.row_cols].astype(bool)
        nnz_expected = len(self.row_cols)
        indptr = mat.indptr
        for row in range(mat.shape[0]):
            a, b = indptr[row], indptr[row + 1]
            if (b - a) != nnz_expected:
                raise ValueError(
                    f"Unexpected sparse pattern in base matrix for row {row}: {b-a} non-zero cells, expected {nnz_expected}"
                )
            if not np.array_equal(mat.indices[a:b], self.row_cols):
                raise ValueError("Base matrix sparse column pattern is not stable across rows.")

    def _load_nc_time_yx(self, path: Path) -> np.ndarray:
        errors: list[str] = []
        for engine in (None, "netcdf4", "h5netcdf", "scipy"):
            ds = None
            try:
                if engine is None:
                    ds = xr.open_dataset(path)
                else:
                    ds = xr.open_dataset(path, engine=engine)
                vname = "T2M" if "T2M" in ds.data_vars else list(ds.data_vars)[0]
                arr = ds[vname].transpose("time", "y", "x").values.astype(np.float32)
                ds.close()
                return arr
            except Exception as exc:
                errors.append(f"{engine or 'auto'} -> {type(exc).__name__}: {exc}")
                if ds is not None:
                    try:
                        ds.close()
                    except Exception:
                        pass

        joined = "\n".join(errors)
        raise RuntimeError(f"Failed to open NetCDF hazard file: {path}\nTried engines:\n{joined}")

    @staticmethod
    def _csr_with_fixed_row_cols(mat: np.ndarray, row_cols: np.ndarray) -> sparse.csr_matrix:
        n_days, n_cells = mat.shape
        rc = np.asarray(row_cols, dtype=np.int32)
        if rc.size == 0:
            return sparse.csr_matrix((n_days, n_cells), dtype=np.float32)
        vals = mat[:, rc].reshape(-1).astype(np.float32, copy=False)
        rows = np.repeat(np.arange(n_days, dtype=np.int32), rc.size)
        cols = np.tile(rc, n_days)
        return sparse.csr_matrix((vals, (rows, cols)), shape=(n_days, n_cells), dtype=np.float32)

    def _load_climate_inputs(self) -> None:
        t2m_var = self.clim_cfg.get("t2m_var", "tas")
        bands_table = self.base_dir / "T2MmeanDeltas/climate_change_provide_markups_bands.csv"
        legacy_table = self.base_dir / self.cfg.get("files", {}).get(
            "t2m_deltas_table", "T2MmeanDeltas/climate_change_provide_markups_avg.csv"
        )
        gcm_table = self.base_dir / "T2MmeanDeltas/climate_change_provide_markups_gcm.csv"

        if bands_table.exists():
            self.climate_delta_path = bands_table
            deltas_df = pd.read_csv(bands_table)
            if "pct_band" not in deltas_df.columns:
                deltas_df["pct_band"] = "central"
        elif legacy_table.exists():
            self.climate_delta_path = legacy_table
            deltas_df = pd.read_csv(legacy_table)
            deltas_df["pct_band"] = "central"
        else:
            raise FileNotFoundError("No climate delta table found (bands or legacy).")

        city_aliases = self.cfg.get("cooling_city_aliases") or [
            self.cfg.get("city_name", self.city),
            self.city,
            self.slug,
            self.slug.capitalize(),
        ]
        city_aliases_low = {str(c).lower() for c in city_aliases}

        self.clim_scens = list(self.clim_cfg.get("t2m_clim_scen_options", ["CurPol", "GS", "SP", "ssp585"]))
        self.clim_bands = list(self.clim_cfg.get("t2m_delta_pct_band_options", ["low", "central", "high"]))

        d = deltas_df.copy()
        d["city_low"] = d["city"].astype(str).str.lower()
        d = d[d["city_low"].isin(city_aliases_low) & (d["var"].astype(str) == str(t2m_var))].copy()

        self.delta_lookup: dict[tuple[str, str, int], np.ndarray] = {}
        for (scen, band, year), grp in d.groupby(["clim_scen", "pct_band", "year"]):
            g = grp.sort_values("month")
            months = g["month"].astype(int).to_numpy()
            if len(np.unique(months)) == 12:
                self.delta_lookup[(str(scen), str(band).lower(), int(year))] = (
                    g.set_index("month").loc[range(1, 13), "delta"].astype(float).to_numpy()
                )
        band_sets: dict[str, set[str]] = {}
        for scen, band, _year in self.delta_lookup.keys():
            band_sets.setdefault(str(scen), set()).add(str(band).lower())
        self.climate_available_bands_by_scenario = {
            str(scen): sorted(list(bands)) for scen, bands in band_sets.items()
        }
        self.climate_forced_central_scenarios = sorted(
            [str(scen) for scen, bands in self.climate_available_bands_by_scenario.items() if set(bands) <= {"central"}]
        )

        self.gcm_lookup: dict[tuple[str, str, int], np.ndarray] = {}
        self.gcm_options = ["__none__"]
        self.climate_gcm_path = gcm_table if gcm_table.exists() else None
        if gcm_table.exists():
            gcm_df = pd.read_csv(gcm_table)
            gcm_df["city_low"] = gcm_df["city"].astype(str).str.lower()
            gcm_df = gcm_df[gcm_df["city_low"].isin(city_aliases_low) & (gcm_df["var"].astype(str) == str(t2m_var))].copy()
            if not gcm_df.empty:
                for (scen, gcm, year), grp in gcm_df.groupby(["clim_scen", "gcm", "year"]):
                    g = grp.sort_values("month")
                    months = g["month"].astype(int).to_numpy()
                    if len(np.unique(months)) == 12:
                        self.gcm_lookup[(str(scen), str(gcm), int(year))] = (
                            g.set_index("month").loc[range(1, 13), "delta"].astype(float).to_numpy()
                        )
                gcms = sorted({str(x) for x in gcm_df["gcm"].astype(str).unique()})
                if gcms:
                    self.gcm_options = gcms

        self.clim_source_options = ["bands"]
        if self.gcm_options != ["__none__"]:
            self.clim_source_options.append("gcm_model")

        self.clim_day_anom_bands: dict[tuple[str, str, int], np.ndarray] = {}
        for scen in self.clim_scens:
            for band in self.clim_bands:
                for y in self.years:
                    months = self.months_by_year[y]
                    d_ref = self.get_monthly_delta(self.ref_scen, self.ref_band, y, None)
                    d_tar = self.get_monthly_delta(scen, band, y, None)
                    self.clim_day_anom_bands[(str(scen), str(band).lower(), y)] = np.asarray(
                        [d_tar[m - 1] - d_ref[m - 1] for m in months], dtype=np.float32
                    )

        self.clim_day_anom_gcm: dict[tuple[str, str, int], np.ndarray] = {}
        if self.gcm_options != ["__none__"]:
            for scen in self.clim_scens:
                for gcm in self.gcm_options:
                    for y in self.years:
                        months = self.months_by_year[y]
                        d_ref = self.get_monthly_delta(self.ref_scen, self.ref_band, y, None)
                        d_tar = self.get_monthly_delta(scen, "central", y, gcm)
                        self.clim_day_anom_gcm[(str(scen), str(gcm), y)] = np.asarray(
                            [d_tar[m - 1] - d_ref[m - 1] for m in months], dtype=np.float32
                        )

    def get_monthly_delta(self, scen: str, band: str, year: int, gcm_model: str | None) -> np.ndarray:
        scen = str(scen)
        band = str(band).lower()
        year = int(year)
        if year <= 2020:
            return np.zeros(12, dtype=float)
        if gcm_model is not None:
            key = (scen, str(gcm_model), year)
            if key in self.gcm_lookup:
                return self.gcm_lookup[key]
        if (scen, band, year) in self.delta_lookup:
            return self.delta_lookup[(scen, band, year)]
        if (scen, "central", year) in self.delta_lookup:
            return self.delta_lookup[(scen, "central", year)]
        if (self.ref_scen, self.ref_band, year) in self.delta_lookup:
            return self.delta_lookup[(self.ref_scen, self.ref_band, year)]
        return np.zeros(12, dtype=float)

    def effective_climate_band(self, scen: str, requested_band: str) -> str:
        scen_key = str(scen)
        req = str(requested_band).lower()
        available = set(self.climate_available_bands_by_scenario.get(scen_key, []))
        if available and req in available:
            return req
        if "central" in available:
            return "central"
        return req

    def _load_exposure_inputs(self) -> None:
        self.expo_manifest = _load_json(self.exp_manifest_path)
        self.direct_years = set(map(int, self.expo_manifest.get("worldpop_direct_years", []))) or {2020, 2030}
        self.exp_ssp_options = sorted(list(self.expo_manifest.get("scenarios", {}).keys()))
        self.population_total_cache: dict[tuple[str | None, int], float] = {}
        self.exp_paths: dict[tuple[str | None, int], Path] = {}
        for y in self.years:
            if y in self.direct_years:
                p = self.resolve_exposure_path(y, None)
                if p is None:
                    raise FileNotFoundError(f"Missing direct exposure for year {y}")
                self.exp_paths[(None, y)] = p
            else:
                for ssp in self.exp_ssp_options:
                    p = self.resolve_exposure_path(y, ssp)
                    if p is None:
                        raise FileNotFoundError(f"Missing scenario exposure for {ssp}, {y}")
                    self.exp_paths[(ssp, y)] = p

    def exposure_candidates(self, year: int, ssp: str | None) -> list[Path]:
        cands: list[Path] = []
        if int(year) == 2020:
            cands += [self.out / f"exposure_with_vulnerability_{self.slug}.h5", self.int_dir / f"exposure_with_vulnerability_{self.slug}.h5"]
        if int(year) == 2030:
            cands += [
                self.out / f"exposure_with_vulnerability_{self.slug}_2030.h5",
                self.int_dir / f"exposure_with_vulnerability_{self.slug}_2030.h5",
            ]
        if ssp is not None:
            cands += [
                self.out / f"exposure_with_vulnerability_{self.slug}_{ssp}_{int(year)}.h5",
                self.out / f"exposure_with_vulnerability_{self.slug}_{ssp.replace('-', '')}_{int(year)}.h5",
                self.int_dir / f"exposure_with_vulnerability_{self.slug}_{ssp}_{int(year)}.h5",
                self.int_dir / f"exposure_with_vulnerability_{self.slug}_{ssp.replace('-', '')}_{int(year)}.h5",
            ]
        seen: set[str] = set()
        out: list[Path] = []
        for cand in cands:
            key = str(cand)
            if key not in seen:
                out.append(cand)
                seen.add(key)
        return out

    def resolve_exposure_path(self, year: int, ssp: str | None) -> Path | None:
        for cand in self.exposure_candidates(year, ssp):
            if cand.exists():
                return cand
        return None

    def _load_ac_inputs(self) -> None:
        ac_cfg = self.cfg.get("ac", {})
        self.ac_cfg = ac_cfg
        self.wh_cfg = ac_cfg.get("waste_heat", {})
        self.ac_ssp_options = [1, 2, 3, 5]
        self.ac_ssp_base = int(ac_cfg.get("ssp", 2))

        pen_file = self.P(ac_cfg.get("penetration_file", ""))
        if not pen_file.exists():
            raise FileNotFoundError(f"Missing AC penetration file: {pen_file}")
        self.ac_penetration_path = pen_file
        pen_df = pd.read_csv(pen_file)
        nuts_cols = ac_cfg.get("nuts_columns", {})
        col_id = nuts_cols.get("id", "NUTS_ID")
        col_scen = nuts_cols.get("scenario", "Scenario")
        col_year = nuts_cols.get("year", "year")
        col_val = nuts_cols.get("value", "value")
        if ac_cfg.get("nuts_id"):
            target_ids = [str(ac_cfg["nuts_id"])]
        else:
            target_ids = [str(x) for x in (ac_cfg.get("penetration_nuts_ids") or ac_cfg.get("kwh_nuts_ids") or [])]
        if not target_ids:
            raise ValueError(f"No NUTS ID configured for {self.slug}")
        pen_city = pen_df[pen_df[col_id].astype(str).isin(target_ids)].copy()
        if pen_city.empty:
            raise ValueError(f"No AC penetration rows matched IDs {target_ids} in {pen_file}")

        self.pen_lookup: dict[tuple[int, int], float] = {}
        for ssp in self.ac_ssp_options:
            scen_label = f"SSP{ssp}"
            sub = pen_city[pen_city[col_scen].astype(str).str.upper() == scen_label.upper()].copy()
            if sub.empty:
                continue
            by_year = sub.groupby(col_year, as_index=False)[col_val].mean().rename(columns={col_year: "year", col_val: "value"})
            for _, row in by_year.iterrows():
                self.pen_lookup[(ssp, int(row["year"]))] = float(row["value"])

        kwh_file = self.P(ac_cfg.get("kwh_file", ""))
        if not kwh_file.exists():
            raise FileNotFoundError(f"Missing AC kWh file: {kwh_file}")
        self.ac_kwh_path = kwh_file
        kwh_df = pd.read_csv(kwh_file)
        kwh_cols = ac_cfg.get("kwh_columns", {})
        kwh_col_id = kwh_cols.get("id", "NUTS_ID")
        kwh_col_scen = kwh_cols.get("scenario", "Scenario")
        kwh_col_year = kwh_cols.get("year", "year")
        kwh_col_val = kwh_cols.get("value", "value")
        if ac_cfg.get("kwh_nuts_ids"):
            kwh_target_ids = [str(x) for x in ac_cfg.get("kwh_nuts_ids", [])]
        else:
            kwh_target_ids = target_ids
        kwh_city = kwh_df[kwh_df[kwh_col_id].astype(str).isin(kwh_target_ids)].copy()
        if kwh_city.empty:
            raise ValueError(f"No AC kWh rows matched IDs {kwh_target_ids} in {kwh_file}")

        self.kwh_lookup: dict[tuple[int, int], float] = {}
        for ssp in self.ac_ssp_options:
            scen_label = f"SSP{ssp}"
            sub = kwh_city[kwh_city[kwh_col_scen].astype(str).str.upper() == scen_label.upper()].copy()
            if sub.empty:
                continue
            by_year = sub.groupby(kwh_col_year, as_index=False)[kwh_col_val].mean().rename(columns={kwh_col_year: "year", kwh_col_val: "value"})
            for _, row in by_year.iterrows():
                self.kwh_lookup[(ssp, int(row["year"]))] = float(row["value"])

        self.wh_enabled_default = bool(self.wh_cfg.get("enabled", True))
        self.wh_enabled_options = _bool_options_with_baseline(
            self.wh_cfg.get("enabled_options", [False, True]), self.wh_enabled_default
        )
        self.wh_lut_options = list(self.wh_cfg.get("lut_case_options", ["low", "central", "high"]))
        self.wh_ratio_min, self.wh_ratio_max = map(float, self.wh_cfg.get("dailymean_from_night_range", [0.33, 0.67]))
        activation_cfg = self.wh_cfg.get("activation", {})
        self.wh_activation_method = str(activation_cfg.get("method", "kou_cdd_share")).lower()
        self.wh_activation_metric = str(activation_cfg.get("temperature_metric", "citymean_dailymean")).lower()
        self.wh_t_on_c = float(activation_cfg.get("t_on_c", 18.0))
        self.wh_t_full_c = float(activation_cfg.get("t_full_c", 25.0))
        self.wh_lut = {float(k): v for k, v in self.wh_cfg.get("lut", {}).items()}
        if not self.wh_lut:
            self.wh_lut = {
                0.00: {"low": 0.00, "central": 0.00, "high": 0.00},
                0.35: {"low": 0.25, "central": 0.375, "high": 0.50},
                0.65: {"low": 0.50, "central": 0.750, "high": 1.00},
                1.00: {"low": 1.00, "central": 1.250, "high": 1.50},
            }

        cop_cfg = self.wh_cfg.get("cop_degradation", {})
        self.cop_enabled_default = bool(cop_cfg.get("enabled", True))
        self.cop_enabled_options = _bool_options_with_baseline(
            cop_cfg.get("enabled_options", [False, True]), self.cop_enabled_default
        )
        self.cop_sens = cop_cfg.get("sensitivity_per_C", {"low": 0.04, "central": 0.065, "high": 0.09})
        self.cop_ref = float(cop_cfg.get("cop_ref", 3.0))
        self.cop_case_options = ["low", "central", "high"]

        cov_candidates = [
            self.int_dir / f"ac_coverage_maps_{self.slug}.npz",
            self.out / f"ac_coverage_maps_{self.slug}.npz",
            self.int_dir / "ac_coverage_maps.npz",
        ]
        cov_path = self._find_first_existing(cov_candidates)
        self.ac_coverage_path = cov_path
        cov_npz = np.load(cov_path)
        years_key = "YEARS_AC" if "YEARS_AC" in cov_npz.files else "years"
        if years_key not in cov_npz.files:
            raise KeyError(f"Could not find coverage years in {cov_path}")
        cov_years = cov_npz[years_key].astype(int)
        if "coverage_base_3d" in cov_npz.files:
            cov_base_3d = cov_npz["coverage_base_3d"].astype(np.float32)
        elif "coverage_base" in cov_npz.files:
            cov_base_3d = cov_npz["coverage_base"][None, ...].astype(np.float32)
        else:
            raise KeyError(f"Could not find baseline coverage arrays in {cov_path}")
        if "coverage_policy_3d" in cov_npz.files:
            cov_policy_3d = cov_npz["coverage_policy_3d"].astype(np.float32)
        elif "coverage_policy" in cov_npz.files:
            cov_policy_3d = cov_npz["coverage_policy"][None, ...].astype(np.float32)
        else:
            cov_policy_3d = cov_base_3d.copy()
        self.coverage_years = cov_years
        self.coverage_raw_by_mode_year: dict[str, dict[int, np.ndarray]] = {"base": {}, "policy": {}}
        self.coverage_pattern_by_mode_year: dict[str, dict[int, np.ndarray]] = {"base": {}, "policy": {}}
        self.coverage_mean_by_mode_year: dict[str, dict[int, float]] = {"base": {}, "policy": {}}

        def _store_coverage(mode: str, cube: np.ndarray) -> None:
            for idx, y in enumerate(cov_years):
                arr = cube[idx if cube.shape[0] > 1 else 0]
                row_vals = arr.ravel()[self.row_cols].astype(np.float32)
                masked = row_vals[self.row_is_city]
                finite = np.isfinite(masked)
                mean_val = float(masked[finite].mean()) if np.any(finite) else 0.0
                # Preserve NB05's missing municipal assignments in the
                # canonical map.  The mortality calculation does not replace
                # these with the finite-pixel mean: it first includes them as
                # zero in a population-weighted city mean and only then fills
                # the gaps with that diluted mean (see
                # ``deaths_year_by_age`` in template Notebook 05 and
                # ``daily_deaths_current_ac`` in template Notebook 06).
                raw = np.full_like(row_vals, np.nan, dtype=np.float32)
                raw_masked = np.where(finite, np.clip(masked, 0.0, 0.98), np.nan)
                raw[self.row_is_city] = raw_masked.astype(np.float32)
                if (not np.isfinite(mean_val)) or mean_val <= 0:
                    pattern = np.ones_like(row_vals, dtype=np.float32)
                else:
                    pattern = np.zeros_like(row_vals, dtype=np.float32)
                    # Some non-Rome coverage rasters carry NaNs inside the city mask
                    # where the municipal downscaling has no cell assignment. Keep the
                    # observed spatial contrast where available and give unassigned city
                    # cells the city-average pattern so one NaN cannot poison the AC branch.
                    masked_clean = np.where(finite, masked, mean_val)
                    masked_clean = np.clip(masked_clean, 0.0, None)
                    pattern[self.row_is_city] = (masked_clean / mean_val).astype(np.float32)
                self.coverage_raw_by_mode_year[mode][int(y)] = raw
                self.coverage_pattern_by_mode_year[mode][int(y)] = pattern
                self.coverage_mean_by_mode_year[mode][int(y)] = mean_val

        _store_coverage("base", cov_base_3d)
        _store_coverage("policy", cov_policy_3d)
        self.coverage_pattern_by_year = self.coverage_pattern_by_mode_year["base"]

        self.ac_cost_params_path = self._find_first_existing(
            [self.int_dir / f"ac_cost_params_{self.slug}.json", self.int_dir / f"ac_costs_{self.slug}.json"]
        )
        self.ac_cost_params = _load_json(self.ac_cost_params_path)
        muni_cov_path = self.out / f"{self.slug}_muni_cov_yearly.csv"
        self.ac_muni_cov_yearly = pd.read_csv(muni_cov_path) if muni_cov_path.exists() else None

        # AC CAPEX / maintenance: the city CONFIG is the canonical source (calibrated).
        # NB09 samples a MULTIPLIER on the configured per-user CAPEX; maintenance is
        # recomputed as configured maint_rate x sampled CAPEX. The NB05 interim JSON is
        # validated against the config and only used as a fallback.
        self.ac_capex_base = float(self.ac_cfg.get("capex_per_user", self.ac_cost_params.get("capex_per_user", 500.0)))
        self.ac_maint_rate = float(self.ac_cfg.get("maint_rate", self.ac_cost_params.get("maint_rate", 0.05)))
        # Validate the NB05 interim JSON against the canonical config (warn-only; config wins).
        for _key, _cfg_val in (
            ("capex_per_user", self.ac_capex_base),
            ("maint_rate", self.ac_maint_rate),
            ("lifetime_years", self.ac_cfg.get("lifetime_years")),
        ):
            _js_val = self.ac_cost_params.get(_key)
            if _cfg_val is not None and _js_val is not None and not np.isclose(float(_js_val), float(_cfg_val), rtol=1e-3, atol=1e-6):
                warnings.warn(f"[{self.slug}] AC {_key}: config={_cfg_val} != NB05 JSON={_js_val}; using config.")

        # AC electricity tariff uncertainty should be anchored to each city config.
        tariff_base = float(
            self.ac_cfg.get(
                "tariff_eur_per_kwh",
                self.ac_cfg.get(
                    "tariff_eur_kwh",
                    self.ac_cost_params.get(
                        "tariff_eur_per_kwh",
                        self.ac_cost_params.get("tariff_eur_kwh", 0.25),
                    ),
                ),
            )
        )
        for _key in ("tariff_eur_per_kwh", "tariff_eur_kwh"):
            _json_tariff = self.ac_cost_params.get(_key)
            if _json_tariff is not None:
                if not np.isclose(float(_json_tariff), tariff_base, rtol=1e-3, atol=1e-6):
                    warnings.warn(
                        f"[{self.slug}] AC tariff: config={tariff_base} != NB05 JSON={_json_tariff}; using config."
                    )
                break
        tariff_opts_cfg = self.ac_cfg.get("tariff_eur_per_kwh_options")
        if tariff_opts_cfg is None:
            tariff_opts_cfg = self.ac_cost_params.get("tariff_eur_per_kwh_options")
        if tariff_opts_cfg is None:
            tariff_opts = [0.85 * tariff_base, tariff_base, 1.15 * tariff_base]
        else:
            tariff_opts = [float(v) for v in tariff_opts_cfg]
        tariff_opts = sorted({round(float(v), 2) for v in tariff_opts if float(v) > 0.0})
        self.ac_tariff_options = tariff_opts if tariff_opts else [round(tariff_base, 2)]

    def _load_ews_inputs(self) -> None:
        self.efficacy_scenarios = list((self.cfg.get("efficacy_scenarios") or {}).keys())
        if not self.efficacy_scenarios:
            raise ValueError("Missing efficacy_scenarios in config.")

        self.ews_cfg = self.cfg.get("ews", {})
        self.ews_marg = self.ews_cfg.get("efficacy_marginal", {})
        self.ews_cf = self.ews_cfg.get("efficacy_counterfactual", {})
        self.ews_overlap = self.ews_cfg.get("ac_overlap_factor", {"low": 0.2, "central": 0.3, "high": 0.4})
        self.ews_disp = self.ews_cfg.get(
            "displacement",
            {
                "<15": {"low": 0.05, "central": 0.10, "high": 0.20},
                "15-64": {"low": 0.05, "central": 0.10, "high": 0.20},
                "65+": {"low": 0.10, "central": 0.25, "high": 0.40},
            },
        )
        self.ews_rly = self.ews_cfg.get("residual_life_years", {"<15": 60, "15-64": 25, "65+": 12})
        self.ews_init = float(self.ews_cfg.get("ramp_initial_efficacy", 0.10))
        self.ews_ramp_base = int(self.ews_cfg.get("ramp_years", 3))
        self.ews_ramp_options = sorted(set([2, self.ews_ramp_base, 5]))
        # EWS effectiveness-INTERPRETATION uncertainty (a UQ axis).
        # NB09 does NOT re-cost the EWS: the city's configured infrastructure and its
        # setup / fixed-opex costs (capex_setup, opex_annual_fixed) are held FIXED as
        # the OBSERVED system. What we vary is how the epidemiological *effectiveness*
        # credited to that infrastructure is interpreted, over a LOCAL bracket = the
        # configured class plus its adjacent class(es) on the
        #   marginal -> intermediate -> counterfactual
        # effectiveness scale. This is a local-classification bracket, not a
        # distribution "centred" on the config (equal-probability categorical
        # sampling has no centre):
        #   marginal       -> {marginal, intermediate}
        #   intermediate   -> {marginal, intermediate, counterfactual}
        #   counterfactual -> {intermediate, counterfactual}
        _interp_scale = ["marginal", "intermediate", "counterfactual"]
        _interp_brackets = {
            "marginal":       ["marginal", "intermediate"],
            "intermediate":   ["marginal", "intermediate", "counterfactual"],
            "counterfactual": ["intermediate", "counterfactual"],
        }
        self.ews_interp_base = str(self.ews_cfg.get("interpretation", "marginal")).lower()
        _interp_center = self.ews_interp_base if self.ews_interp_base in _interp_scale else "marginal"
        if bool(self.ews_cfg.get("uq_interp_full_range", False)):
            # Escape hatch: sample the full marginal->intermediate->counterfactual range for every city.
            self.ews_interp_options = list(_interp_scale)
        else:
            self.ews_interp_options = list(_interp_brackets[_interp_center])
        self.level_options = ["low", "central", "high"]
        self.ews_cost_model_options = ["pavanello", "chiabai"]
        self.ews_target_base = int(self.ews_cfg.get("target_activation_days", 23))
        self.ews_target_days_options = sorted(
            set(int(x) for x in (self.ews_cfg.get("target_activation_days_options") or [self.ews_target_base]))
        )
        self.ews_recalib_base = int(self.ews_cfg.get("threshold_recalib_interval", 5))
        self.ews_recalib_options = sorted(
            set(max(1, int(x)) for x in (self.ews_cfg.get("threshold_recalib_interval_options") or [3, 5, 7]))
        )
        self.ews_threshold_ref_year = int(self.ews_cfg.get("threshold_ref_year", min(self.years)))
        self.ews_nonpositive_fallback = str(self.ews_cfg.get("nonpositive_threshold_fallback", "none")).strip().lower()
        self.ews_warning_trigger_mode = (
            "event_mask"
            if (self.use_extreme_track and self.ews_nonpositive_fallback == "event_mask")
            else "deaths_threshold"
        )

        season_cfg = self.ews_cfg.get("warning_season", {}) or {}
        if season_cfg.get("start_md") and season_cfg.get("end_md"):
            self.season_start_md = str(season_cfg["start_md"])
            self.season_end_md = str(season_cfg["end_md"])
        else:
            warning_months = list(self.ews_cfg.get("warning_months", [5, 6, 7, 8, 9]))
            start_month = min(warning_months)
            end_month = max(warning_months)
            self.season_start_md = f"{start_month:02d}-01"
            end_date = pd.Timestamp(year=2021, month=end_month, day=1) + pd.offsets.MonthEnd(0)
            self.season_end_md = f"{end_month:02d}-{end_date.day:02d}"

        thr_candidates = [
            self.int_dir / f"ews_threshold_deaths_{self.slug}.json",
            self.int_dir / f"ews_threshold_deaths_{self.slug}_climate_only.json",
        ]
        thr_path = next((p for p in thr_candidates if p.exists()), None)
        self.threshold_meta_path = str(thr_path) if thr_path is not None else None
        if thr_path is None:
            if self.ews_warning_trigger_mode == "deaths_threshold":
                raise FileNotFoundError("Could not find any candidate file:\n" + "\n".join(str(p) for p in thr_candidates))
            self.threshold_meta = {}
        else:
            self.threshold_meta = _load_json(thr_path)
        if self.ews_warning_trigger_mode == "event_mask":
            self.threshold_meta = {
                **self.threshold_meta,
                "legacy_unused_in_nb09": True,
                "nb09_warning_trigger_mode": "event_mask",
            }

        warn_days_path = self.tab_dir / f"{self.slug}_ews_warning_days.csv"
        self.ews_warning_days_path = warn_days_path if warn_days_path.exists() else None
        if warn_days_path.exists():
            self.warn_days_df = pd.read_csv(warn_days_path)
        else:
            self.warn_days_df = pd.DataFrame()

        self.ews_params_path = self.tab_dir / f"{self.slug}_ews_parameters.json"
        self.ews_params = _load_json(self.ews_params_path) if self.ews_params_path.exists() else {}

    def _load_tree_inputs(self) -> None:
        self.trees_cfg = self.cfg.get("trees", {})
        veg_path = self.int_dir / f"{self.slug}_veg_aux_arrays.npz"
        if not veg_path.exists():
            raise FileNotFoundError(f"Missing vegetation aux arrays from NB07: {veg_path}")
        self.tree_veg_aux_path = veg_path
        veg = np.load(veg_path)
        required = {"dLST_month_ref_uniform", "gvi_baseline", "SCOPE_PHYS"}
        missing = sorted(required.difference(veg.files))
        if missing:
            raise KeyError(f"{veg_path} is missing canonical NB07 vegetation arrays: {missing}")
        tree_dlst_full = veg["dLST_month_ref_uniform"].reshape(12, -1).astype(np.float32)
        self.tree_dlst_month_maps = tree_dlst_full[:, self.row_cols].astype(np.float32)
        self.tree_gvi_0_1 = (veg["gvi_baseline"].reshape(-1)[self.row_cols].astype(np.float32) / 100.0)
        self.tree_scope = veg["SCOPE_PHYS"].reshape(-1)[self.row_cols].astype(bool)
        self.tree_dlst_month_maps[:, ~self.tree_scope] = 0.0
        # NB08 currently uses this NB07-exported city-mean approximation only
        # for the second-order lambda_y waste-heat interaction.  Primary tree
        # mortality branches below use the exact day-specific quadratic bridge.
        self.tree_lambda_dt2m_monthly = None
        if "dT2M_month_ref_uniform_approx" in veg.files:
            lambda_maps = veg["dT2M_month_ref_uniform_approx"].reshape(12, -1)[:, self.row_cols]
            self.tree_lambda_dt2m_monthly = np.array(
                [float(np.nanmean(lambda_maps[m, self.tree_scope])) for m in range(12)],
                dtype=float,
            )

        expected_shape = (self.hgt, self.wdt)
        lcz_full = None
        lcz_path = None
        cached_candidates = [
            self.int_dir / f"lcz_on_ref_{self.slug}.tif",
            self.out / f"{self.slug}_lcz_on_ref.tif",
        ]
        for candidate in cached_candidates:
            if not candidate.exists():
                continue
            with rio.open(candidate) as src:
                if src.shape == expected_shape:
                    lcz_full = src.read(1)
                    lcz_path = candidate
                    break

        if lcz_full is None:
            # NB07 does not always persist its in-memory aligned LCZ raster.
            # Reproduce that notebook's nearest-neighbour reprojection from
            # the active config instead of accepting a differently gridded
            # reporting raster such as lcz_masked_fua.tif.
            configured = [self.P(str(path)) for path in (self.cfg.get("files", {}).get("lcz_candidates", []) or [])]
            raw_candidates = configured + [
                self.P("LCZ/lcz_filter_v3.tif"),
                self.P("LCZ/lcz_v3.tif"),
            ]
            lcz_path = next((path for path in raw_candidates if path.exists()), None)
            if lcz_path is None:
                raise FileNotFoundError(
                    "Could not find the NB07 LCZ source raster:\n" + "\n".join(str(path) for path in raw_candidates)
                )
            with rio.open(lcz_path) as src:
                src_nodata = src.nodata if src.nodata is not None else 0
                with WarpedVRT(
                    src,
                    crs=self.ref_crs,
                    transform=self.ref_transform,
                    width=self.wdt,
                    height=self.hgt,
                    resampling=Resampling.nearest,
                    src_nodata=src_nodata,
                    nodata=np.nan,
                ) as vrt:
                    reproj = vrt.read(1, out_dtype="float32")
            work = np.where(self.city_mask, reproj, np.nan)
            missing_inside = np.isnan(work) & self.city_mask
            if np.any(missing_inside):
                valid = np.isfinite(work) & self.city_mask
                if not np.any(valid):
                    raise ValueError(f"LCZ reprojection from {lcz_path} has no valid city cells.")
                _, (iy, ix) = ndi.distance_transform_edt(~valid, return_indices=True)
                work[missing_inside] = work[iy[missing_inside], ix[missing_inside]]
            lcz_full = np.where(self.city_mask, work, 0).astype(np.int16)

        if lcz_full.shape != expected_shape:
            raise ValueError(f"NB07 LCZ raster {lcz_path} has shape {lcz_full.shape}, expected {expected_shape}.")
        self.tree_lcz_path = lcz_path
        self.tree_lcz = lcz_full.reshape(-1)[self.row_cols]

        emulator_rel = self.trees_cfg.get(
            "veg_emulator_bundle",
            f"emulator/bundles/emulator_bundle_{self.slug}_quadratic_holdout_safe.json",
        )
        emulator_path = self.out / str(emulator_rel)
        if not emulator_path.exists():
            candidates = sorted((self.out / "emulator" / "bundles").glob("emulator_bundle_*.json"))
            if len(candidates) != 1:
                raise FileNotFoundError(
                    f"Configured NB07 emulator bundle is missing ({emulator_path}) and a unique fallback was not found."
                )
            emulator_path = candidates[0]
            warnings.warn(f"[{self.slug}] Using sole emulator bundle fallback: {emulator_path.name}")
        self.tree_emulator_path = emulator_path
        self.tree_emulator = _load_json(emulator_path)
        self.tree_base_cap = float(self.trees_cfg.get("cap_uplift_0_1", 0.12))
        self.tree_ramp_base = int(self.trees_cfg.get("ramp_years", 12))
        self.tree_ramp_options = sorted(set([8, self.tree_ramp_base, 15]))
        self.tree_start_age_options = [0, 5]
        self.tree_cost_params_path = self._find_first_existing(
            [self.int_dir / f"tree_cost_params_{self.slug}.json", self.tab_dir / f"tree_cost_params_{self.slug}.json"]
        )
        self.tree_cost_params = _load_json(self.tree_cost_params_path)

        # Electricity feedback (Falchetta, De Cian and Lunghi 2026)
        self.elec_fb_cfg = self.cfg.get("electricity_feedback", {})
        self.elec_fb_enabled = bool(self.elec_fb_cfg.get("enabled", False))
        from cityheat.electricity_feedback import resolve_pct_gvi_reduction as _rpgr; self.elec_fb_pct_per_point = _rpgr(self.cfg, self.base_dir, self.int_dir)[0]  # runtime per-city GVI-elec calibration from JJA daily-max T2M (Falchetta Fig-5)
        self.elec_fb_summer_months = int(self.elec_fb_cfg.get("summer_months", 3))
        self.elec_fb_co2_per_kwh = float(self.elec_fb_cfg.get("co2_intensity_gCO2_per_kwh", 372))
        self.elec_fb_ac_summary = None
        self.elec_fb_cov_yearly = None
        self.elec_fb_dgvi_by_region = None
        if self.elec_fb_enabled:
            cov_yearly_path = self.out / f"{self.slug}_muni_cov_yearly.csv"
            if cov_yearly_path.exists():
                self.elec_fb_cov_yearly = pd.read_csv(cov_yearly_path)
            ac_summary_path = self.out / f"{self.slug}_muni_ac_consumption_summary.csv"
            if ac_summary_path.exists():
                self.elec_fb_ac_summary = pd.read_csv(ac_summary_path)
            # Load region-level dGVI from trees table
            trees_cfg = self.cfg.get("trees", {})
            veg_label = str(trees_cfg.get("veg_region_label", "region")).lower()
            dgvi_path = self.tab_dir / f"{self.slug}_trees_{veg_label}.csv"
            if dgvi_path.exists():
                dgvi_df = pd.read_csv(dgvi_path)
                id_col = "region_id" if "region_id" in dgvi_df.columns else dgvi_df.columns[0]
                dgvi_col = "dGVI_points" if "dGVI_points" in dgvi_df.columns else "dGVI"
                self.elec_fb_dgvi_by_region = dict(zip(dgvi_df[id_col].astype(int), dgvi_df[dgvi_col].astype(float)))

        # Precompute pop-weighted mean dGVI for city-level reduction
        self.elec_fb_pw_dgvi = 0.0
        if self.elec_fb_enabled and self.elec_fb_dgvi_by_region and self.elec_fb_ac_summary is not None:
            ac_df = self.elec_fb_ac_summary
            first_year = ac_df["year"].min()
            yr_df = ac_df[ac_df["year"] == first_year]
            total_pop = 0.0
            weighted_dgvi = 0.0
            for _, row in yr_df.iterrows():
                mid = int(row["muni_id"])
                pop = float(row.get("pop_muni", row.get("users_muni", 0)))
                dgvi = self.elec_fb_dgvi_by_region.get(mid, 0.0)
                weighted_dgvi += pop * max(dgvi, 0.0)
                total_pop += pop
            if total_pop > 0:
                self.elec_fb_pw_dgvi = weighted_dgvi / total_pop

    def _load_vulnerability_baseline(self) -> None:
        """Load baseline vulnerability components and DRMKC/GVI series for on-the-fly SVI recomputation."""
        self.vuln_cfg = get_vuln_config(self.cfg)
        dyn_cfg = self.vuln_cfg.get("dynamic", {})

        # Load baseline vulnerability arrays (thermal, foreign_abs, unemp_abs)
        base_vuln = load_vulnerability(self.int_dir, slug=self.slug)
        self.vuln_thermal0 = base_vuln["thermal"].astype(np.float32)
        self.vuln_foreign0_abs = base_vuln.get("foreign_abs", base_vuln["foreign"]).astype(np.float32)
        self.vuln_unemp0_abs = base_vuln.get("unemp_abs", base_vuln["unemp"]).astype(np.float32)

        # Baseline means (city-mask-weighted)
        cm = self.city_mask
        self.vuln_foreign_mean0 = float(np.nanmean(self.vuln_foreign0_abs[cm & np.isfinite(self.vuln_foreign0_abs)]))
        self.vuln_unemp_mean0 = float(np.nanmean(self.vuln_unemp0_abs[cm & np.isfinite(self.vuln_unemp0_abs)]))

        # Preload population grids for all modeled years
        self.vuln_pop_grids: dict[tuple[str | None, int], np.ndarray] = {}
        pop_base_year = int(dyn_cfg.get("population_baseline_year", 2020))
        self.vuln_pop_base = _load_population_array(self.int_dir, pop_base_year, scenario=None)
        self.vuln_pop_grids[(None, pop_base_year)] = self.vuln_pop_base
        anchor_year = int(dyn_cfg.get("drmkc", {}).get("anchor_year", 2030))

        # Load direct worldpop years
        for year_str in self.expo_manifest.get("direct_worldpop", {}).keys():
            year = int(year_str)
            if (None, year) not in self.vuln_pop_grids:
                try:
                    self.vuln_pop_grids[(None, year)] = _load_population_array(self.int_dir, year, scenario=None)
                except FileNotFoundError:
                    pass

        # Load scenario years
        for scen, year_map in self.expo_manifest.get("scenarios", {}).items():
            for year_str in year_map.keys():
                year = int(year_str)
                key = (str(scen), year)
                if key not in self.vuln_pop_grids:
                    try:
                        self.vuln_pop_grids[key] = _load_population_array(self.int_dir, year, scenario=scen)
                    except FileNotFoundError:
                        pass

        # Load DRMKC component series
        base_path = self.base
        self.vuln_drmkc_foreign = _load_drmkc_component_series(self.vuln_cfg, "foreign_born", base_path=base_path)
        self.vuln_drmkc_unemp = _load_drmkc_component_series(self.vuln_cfg, "unemployment", base_path=base_path)

        # Load GVI series (one per exposure SSP)
        self.vuln_gvi_cache: dict[str, dict] = {}
        for ssp in self.exp_ssp_options:
            self.vuln_gvi_cache[str(ssp)] = _load_gvi_series(self.vuln_cfg, str(ssp), self.cfg, base_path=base_path)

        # Vulnerability config defaults for parameter ranges
        self.vuln_k_default = float(dyn_cfg.get("k", {}).get("default", 0.80))
        self.vuln_phi_2050_default = float(dyn_cfg.get("phi", {}).get("default_2050", 0.80))
        fb_proj = dyn_cfg.get("foreign_born_projection", {})
        ue_proj = dyn_cfg.get("unemployment_projection", {})
        self.vuln_drmkc_fb_default = float(fb_proj.get("drmkc_scale", 0.04))
        self.vuln_drmkc_ue_default = float(ue_proj.get("drmkc_scale", 0.08))
        self.vuln_gvi_fb_default = float(fb_proj.get("gvi_scale", 0.35))
        self.vuln_gvi_ue_default = float(ue_proj.get("gvi_scale", 0.50))
        therm_proj = dyn_cfg.get("thermal_projection", {})
        self.vuln_retrofit_default = float(therm_proj.get("retrofit_rate_per_year", 0.01))

    def recompute_projected_svi(
        self,
        year: int,
        scenario: str | None,
        sample: dict[str, Any],
    ) -> dict[str, np.ndarray]:
        """Recompute projected SVI for a given year using sampled vulnerability parameters.

        Returns a dict with keys: svi, thermal, foreign, unemp.
        """
        from copy import deepcopy

        # Build a modified vuln_cfg with the sampled parameters
        vuln_cfg = deepcopy(self.vuln_cfg)
        dyn_cfg = vuln_cfg["dynamic"]

        k_val = float(sample["VULN_K"])
        phi_2050 = float(sample["VULN_PHI_2050"])
        drmkc_fb = float(sample["VULN_DRMKC_SCALE_FB"])
        drmkc_ue = float(sample["VULN_DRMKC_SCALE_UE"])
        gvi_fb = float(sample["VULN_GVI_SCALE_FB"])
        gvi_ue = float(sample["VULN_GVI_SCALE_UE"])
        retrofit_rate = float(sample["VULN_RETROFIT_RATE"])
        growth_sens = float(sample["VULN_GROWTH_SENS"])
        growth_cap = float(sample["VULN_GROWTH_CAP"])
        new_build_vuln = float(sample["VULN_NEW_BUILD"])

        dyn_cfg["k"] = {"default": k_val, "foreign_born": k_val, "unemployment": k_val}
        # Keep phi_2030 at config default; only vary phi_2050
        phi_2030_default = float(self.vuln_cfg.get("dynamic", {}).get("phi", {}).get("default_2030", 0.95))
        dyn_cfg["phi"] = {
            "default_2030": phi_2030_default,
            "default_2050": phi_2050,
            "foreign_born_2030": phi_2030_default,
            "foreign_born_2050": phi_2050,
            "unemployment_2030": phi_2030_default,
            "unemployment_2050": phi_2050,
        }
        dyn_cfg["foreign_born_projection"]["drmkc_scale"] = drmkc_fb
        dyn_cfg["foreign_born_projection"]["gvi_scale"] = gvi_fb
        dyn_cfg["unemployment_projection"]["drmkc_scale"] = drmkc_ue
        dyn_cfg["unemployment_projection"]["gvi_scale"] = gvi_ue
        dyn_cfg["thermal_projection"]["retrofit_rate_per_year"] = retrofit_rate
        dyn_cfg["thermal_projection"]["growth_sensitivity"] = growth_sens
        dyn_cfg["thermal_projection"]["growth_cap"] = growth_cap
        dyn_cfg["thermal_projection"]["new_build_vulnerability"] = new_build_vuln

        anchor_year = int(dyn_cfg.get("drmkc", {}).get("anchor_year", 2030))

        # Resolve population grid for this year/scenario
        if year <= anchor_year or scenario is None:
            pop_key = (None, year)
        else:
            pop_key = (str(scenario), year)
        if pop_key not in self.vuln_pop_grids:
            # Fallback: try direct year without scenario
            pop_key = (None, year)
        if pop_key not in self.vuln_pop_grids:
            # Last resort: nearest available grid by year
            pop_key = min(self.vuln_pop_grids.keys(), key=lambda k: abs(k[1] - year))
            import warnings
            warnings.warn(
                f"Vulnerability pop grid missing for year={year}, scenario={scenario}; "
                f"falling back to {pop_key}.",
                stacklevel=2,
            )
        pop_target = self.vuln_pop_grids[pop_key]

        # Resolve GVI series for long-run projection
        gvi_series = {}
        if scenario is not None and year > anchor_year:
            gvi_series = self.vuln_gvi_cache.get(str(scenario), {})

        # Recompute thermal component
        thermal = _project_thermal_component(
            self.vuln_thermal0, self.vuln_pop_base, pop_target,
            self.city_mask, year, scenario, vuln_cfg,
        )

        # Recompute foreign_born component
        phi_fb = _phi_for_year(dyn_cfg, "foreign_born", year)
        foreign_mean_new = _project_component_mean(
            self.vuln_foreign_mean0, year, "foreign_born", scenario,
            vuln_cfg, self.cfg, self.vuln_drmkc_foreign, gvi_series,
        )
        foreign = _project_absolute_component_grid(
            self.vuln_foreign0_abs, self.vuln_foreign_mean0, foreign_mean_new,
            phi_fb, self.city_mask,
        )

        # Recompute unemployment component
        phi_ue = _phi_for_year(dyn_cfg, "unemployment", year)
        unemp_mean_new = _project_component_mean(
            self.vuln_unemp_mean0, year, "unemployment", scenario,
            vuln_cfg, self.cfg, self.vuln_drmkc_unemp, gvi_series,
        )
        unemp = _project_absolute_component_grid(
            self.vuln_unemp0_abs, self.vuln_unemp_mean0, unemp_mean_new,
            phi_ue, self.city_mask,
        )

        # Compute composite SVI with config weights and operational validity rule
        min_comp = int(self.vuln_cfg.get("svi_min_valid_components", 1))
        svi = compute_svi(
            thermal,
            foreign,
            unemp,
            self.city_mask,
            self.vuln_cfg["weights"],
            min_valid_components=min_comp,
        )

        return {"svi": svi, "thermal": thermal, "foreign": foreign, "unemp": unemp}

    def compute_vulnerability_metrics(
        self,
        year: int,
        scenario: str | None,
        sample: dict[str, Any],
        pop_grid: np.ndarray | None = None,
    ) -> dict[str, float]:
        """Compute summary vulnerability metrics for a single year/scenario/sample draw."""
        vuln = self.recompute_projected_svi(year, scenario, sample)
        cm = self.city_mask
        svi = vuln["svi"]
        valid = cm & np.isfinite(svi)
        svi_vals = svi[valid]

        if svi_vals.size == 0:
            return {
                **{k: np.nan for k in [
                    "svi_mean", "svi_p10", "svi_p90", "svi_p90_p10_gap",
                    "pop_weighted_svi", "thermal_mean", "foreign_born_mean", "unemp_mean",
                ]},
                "pop_weighted_svi_is_weighted": False,
            }

        svi_p10 = float(np.nanpercentile(svi_vals, 10))
        svi_p90 = float(np.nanpercentile(svi_vals, 90))

        # Population-weighted SVI
        pop_weighted = False
        if pop_grid is not None:
            pop_v = pop_grid[valid].astype(float)
            pop_sum = float(np.nansum(pop_v))
            if pop_sum > 0:
                pw_svi = float(np.nansum(svi_vals * pop_v) / pop_sum)
                pop_weighted = True
            else:
                pw_svi = float(np.nanmean(svi_vals))
        else:
            pw_svi = float(np.nanmean(svi_vals))
        if not pop_weighted:
            import warnings
            warnings.warn(
                f"pop_weighted_svi for year={year} scenario={scenario} is unweighted "
                f"({'no population grid available' if pop_grid is None else 'population grid sums to zero'}).",
                stacklevel=2,
            )

        def _comp_mean(arr: np.ndarray) -> float:
            m = cm & np.isfinite(arr)
            return float(np.nanmean(arr[m])) if np.any(m) else np.nan

        return {
            "svi_mean": float(np.nanmean(svi_vals)),
            "svi_p10": svi_p10,
            "svi_p90": svi_p90,
            "svi_p90_p10_gap": svi_p90 - svi_p10,
            "pop_weighted_svi": pw_svi,
            "pop_weighted_svi_is_weighted": pop_weighted,
            "thermal_mean": _comp_mean(vuln["thermal"]),
            "foreign_born_mean": _comp_mean(vuln["foreign"]),
            "unemp_mean": _comp_mean(vuln["unemp"]),
        }

    def _build_param_specs(self) -> None:
        self.param_specs = [
            ParamSpec("YEAR_IDX", "choice", options=list(range(len(self.years)))),
            ParamSpec("EXP_SSP_IDX", "choice", options=list(range(max(len(self.exp_ssp_options), 1)))),
            ParamSpec("EXP_TOTAL_SCALE", "uniform", low=0.90, high=1.10),
            ParamSpec("BASELINE_MODE_IDX", "choice", options=list(range(len(self.baseline_modes)))),
            ParamSpec("CLIM_SCEN_IDX", "choice", options=list(range(len(self.clim_scens)))),
            ParamSpec("CLIM_BAND_IDX", "choice", options=list(range(len(self.clim_bands)))),
            ParamSpec("CLIM_SOURCE_IDX", "choice", options=list(range(len(self.clim_source_options)))),
            ParamSpec("GCM_MODEL_IDX", "choice", options=list(range(len(self.gcm_options)))),
            ParamSpec("AC_SSP_IDX", "choice", options=list(range(len(self.ac_ssp_options)))),
            ParamSpec("WH_ENABLED_IDX", "choice", options=list(range(len(self.wh_enabled_options)))),
            ParamSpec("WH_LUT_CASE_IDX", "choice", options=list(range(len(self.wh_lut_options)))),
            ParamSpec("WH_RATIO", "uniform", low=self.wh_ratio_min, high=self.wh_ratio_max),
            ParamSpec("COP_ENABLED_IDX", "choice", options=list(range(len(self.cop_enabled_options)))),
            ParamSpec("COP_CASE_IDX", "choice", options=list(range(len(self.cop_case_options)))),
            ParamSpec("TREE_COEFF_SCALE", "uniform", low=0.50, high=1.50),
            ParamSpec("TREE_CAP_UPLIFT", "uniform", low=0.06, high=0.20),
            ParamSpec("TREE_RAMP_YEARS_IDX", "choice", options=list(range(len(self.tree_ramp_options)))),
            ParamSpec("TREE_START_AGE_IDX", "choice", options=list(range(len(self.tree_start_age_options)))),
            ParamSpec("IF_FAMILY_IDX", "choice", options=list(range(len(self.available_if_families)))),
            ParamSpec("IF_TREF_IDX", "choice", options=list(range(len(TREF_OPTIONS)))),
            ParamSpec("MDD_SCALE_LT15", "uniform", low=0.80, high=1.20),
            ParamSpec("MDD_SCALE_15_64", "uniform", low=0.80, high=1.20),
            ParamSpec("MDD_SCALE_65P", "uniform", low=0.80, high=1.20),
            ParamSpec("DISP_FRAC", "uniform", low=0.00, high=0.30),
            ParamSpec("PAA_SCALE", "uniform", low=0.80, high=1.00),
            ParamSpec("AC_EFF_SCEN_IDX", "choice", options=list(range(len(self.efficacy_scenarios)))),
            ParamSpec("EWS_INTERP_IDX", "choice", options=list(range(len(self.ews_interp_options)))),
            ParamSpec("EWS_CF_EFF_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_EFF_LT15_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_EFF_15_64_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_EFF_65P_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_OVERLAP_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_DISP_LT15_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_DISP_15_64_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_DISP_65P_LEVEL_IDX", "choice", options=list(range(len(self.level_options)))),
            ParamSpec("EWS_RAMP_YEARS_IDX", "choice", options=list(range(len(self.ews_ramp_options)))),
            ParamSpec("EWS_COST_MODEL_IDX", "choice", options=list(range(len(self.ews_cost_model_options)))),
            ParamSpec("DISCOUNT_RATE_IDX", "choice", options=[0.02, 0.03, 0.05]),
            ParamSpec("AC_CAPEX_MULT_IDX", "choice", options=[0.8, 1.0, 1.2]),
            ParamSpec("AC_TARIFF_EUR_PER_KWH_IDX", "choice", options=self.ac_tariff_options),
            ParamSpec("AC_LIFETIME_YEARS_IDX", "choice", options=[9, 12, 16]),
            ParamSpec("TREE_CAPEX_MULT_IDX", "choice", options=[0.8, 1.0, 1.2]),
            ParamSpec("TREE_OM_MULT_IDX", "choice", options=[1.0, 5.0]),
            # Electricity feedback (Falchetta et al. 2026)
            ParamSpec("ELEC_FEEDBACK_ENABLED_IDX", "choice", options=[0, 1]),
            ParamSpec("ELEC_COEFF_SCALE", "uniform", low=0.50, high=1.50),
            # Vulnerability projection parameters (Level A: uncertainty only, does not affect mortality)
            ParamSpec("VULN_K", "uniform", low=0.55, high=0.95),
            ParamSpec("VULN_PHI_2050", "uniform", low=0.50, high=0.90),
            ParamSpec("VULN_DRMKC_SCALE_FB", "uniform", low=0.02, high=0.08),
            ParamSpec("VULN_DRMKC_SCALE_UE", "uniform", low=0.04, high=0.16),
            ParamSpec("VULN_GVI_SCALE_FB", "uniform", low=0.15, high=0.55),
            ParamSpec("VULN_GVI_SCALE_UE", "uniform", low=0.25, high=0.75),
            ParamSpec("VULN_RETROFIT_RATE", "uniform", low=0.005, high=0.020),
            ParamSpec("VULN_GROWTH_SENS", "uniform", low=0.50, high=1.00),   # expert (config central ~0.7-0.8)
            ParamSpec("VULN_GROWTH_CAP", "uniform", low=0.25, high=0.45),    # Eurostat completion rates ~0.7-1%/yr -> ~20-45% cumulative
            ParamSpec("VULN_NEW_BUILD", "uniform", low=0.10, high=0.25),     # central 0.15 (EPBD nZEB); upper tail = nZEB summer overheating
        ]
        if self.ews_uses_event_mask_warning():
            # Track-B event-mask mode: keep only active warning-trigger dimensions.
            self.param_specs += [
                ParamSpec(
                    "EXTREME_THRESHOLD_PCT_IDX",
                    "choice",
                    options=list(range(len(self.extreme_threshold_options))),
                ),
                ParamSpec(
                    "EXTREME_MIN_DURATION_IDX",
                    "choice",
                    options=list(range(len(self.extreme_min_duration_options))),
                ),
            ]
        else:
            # Standard deaths-threshold mode.
            self.param_specs += [
                ParamSpec("EWS_TARGET_DAYS_IDX", "choice", options=list(range(len(self.ews_target_days_options)))),
                ParamSpec("EWS_RECALIB_YEARS_IDX", "choice", options=list(range(len(self.ews_recalib_options)))),
            ]
        self.problem = {
            "num_vars": len(self.param_specs),
            "names": [spec.name for spec in self.param_specs],
            "bounds": [
                [0.0, float(len(spec.options) - 1 if spec.options is not None else spec.high)] if spec.kind == "choice" else [float(spec.low), float(spec.high)]
                for spec in self.param_specs
            ],
        }

    def sample_parameters(self, n: int, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
        sampler = qmc.LatinHypercube(d=len(self.param_specs), seed=seed)
        u = sampler.random(int(n))
        cols: dict[str, np.ndarray] = {}
        x_cols: list[np.ndarray] = []
        for idx, spec in enumerate(self.param_specs):
            if spec.kind == "choice":
                n_opt = len(spec.options or [])
                arr = np.minimum((u[:, idx] * n_opt).astype(int), n_opt - 1)
                cols[spec.name] = arr
                x_cols.append(arr.astype(float))
            else:
                arr = float(spec.low) + u[:, idx] * (float(spec.high) - float(spec.low))
                cols[spec.name] = arr.astype(float)
                x_cols.append(arr.astype(float))
        x = np.column_stack(x_cols)
        return pd.DataFrame(cols), x

    def _git_provenance(self) -> dict[str, Any]:
        def run_git(*args: str) -> str | None:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            return completed.stdout.strip() if completed.returncode == 0 else None

        commit = run_git("rev-parse", "HEAD")
        status = run_git("status", "--porcelain", "--untracked-files=no")
        return {
            "commit": commit,
            "tracked_worktree_dirty": bool(status),
            "tracked_worktree_status": status.splitlines() if status else [],
        }

    def _configured_existing_files(self) -> set[Path]:
        """Find file-valued config entries relative to the city data/root paths."""
        files: set[Path] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
            elif isinstance(value, str) and value.strip():
                raw = Path(value).expanduser()
                candidates = [raw] if raw.is_absolute() else [self.base_dir / raw, self.root / raw]
                for candidate in candidates:
                    try:
                        candidate = candidate.resolve()
                    except OSError:
                        continue
                    if candidate.is_file():
                        files.add(candidate)
                        break

        visit(self.cfg)
        return files

    def input_files(self) -> list[Path]:
        """Return the concrete model inputs whose contents define this run."""
        files: set[Path] = set(self._configured_existing_files())
        direct_paths: list[Path | None] = [
            self.cfg_path,
            self.root / "data_manifests" / f"{self.slug}_gdrive.json",
            self.template_tif,
            self.city_mask_npz,
            self.exp_manifest_path,
            self.haz_events_csv,
            getattr(self, "climate_delta_path", None),
            getattr(self, "climate_gcm_path", None),
            getattr(self, "ac_penetration_path", None),
            getattr(self, "ac_kwh_path", None),
            getattr(self, "ac_coverage_path", None),
            self.ac_cost_params_path,
            Path(self.threshold_meta_path) if self.threshold_meta_path else None,
            getattr(self, "ews_warning_days_path", None),
            self.ews_params_path,
            getattr(self, "tree_veg_aux_path", None),
            getattr(self, "tree_lcz_path", None),
            self.tree_emulator_path,
            self.tree_cost_params_path,
            self.tab_dir / f"annual_heat_deaths_baseline_current_ac_{self.slug}.csv",
            self.tab_dir / f"annual_heat_deaths_avoided_EWS_{self.slug}.csv",
            self.tab_dir / f"trees_benefits_25y_{self.slug}.csv",
            self.tab_dir / f"ews_benefits_25y_{self.slug}.csv",
            self.tab_dir / f"{self.slug}_cba_summary.json",
            self.root / "cityheat" / "nb09_improved_fast.py",
            self.root / "cityheat" / "nb09_improved_fast_masselot_main.py",
            self.root / "notebooks" / "city_agnostic" / "March2026_agnostic" / "template" / "09_uncertainty_0126_improved_fast.ipynb",
            self.root / "scripts" / "run_agnostic_batch.py",
            self.root / "scripts" / "juno_run_nb09.sh",
        ]
        files.update(path.resolve() for path in direct_paths if path is not None and path.is_file())
        files.update(path.resolve() for path in self.if_jsons.values() if path.is_file())
        files.update(path.resolve() for path in self.exp_paths.values() if path.is_file())
        for mode in self.baseline_modes:
            for year in self.years:
                path = self.tagged_haz_path(int(year), mode)
                if path.is_file():
                    files.add(path.resolve())
        # These generated arrays are direct inputs to the dynamic-vulnerability
        # calculations even when their original sources are outside the city config.
        files.update(path.resolve() for path in self.int_dir.glob("vulnerability_*.npz") if path.is_file())
        files.update(path.resolve() for path in self.int_dir.glob("population_*.npz") if path.is_file())
        return sorted(files, key=lambda path: str(path))

    def input_fingerprints(
        self,
        previous: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        previous_by_path = {
            str(item.get("path")): item
            for item in (previous or [])
            if isinstance(item, dict) and item.get("path")
        }
        fingerprints: list[dict[str, Any]] = []
        for path in self.input_files():
            stat = path.stat()
            try:
                logical_path = str(path.relative_to(self.root))
            except ValueError:
                logical_path = str(path)
            prior = previous_by_path.get(logical_path)
            if (
                prior is not None
                and int(prior.get("size_bytes", -1)) == int(stat.st_size)
                and int(prior.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
                and (prior.get("digest") or prior.get("sha256"))
            ):
                fingerprints.append(prior)
                continue
            if os.environ.get("NB09_FINGERPRINT_PROGRESS", "0").strip() == "1":
                print(f"[{self.slug}] fingerprinting input: {logical_path}", flush=True)
            fingerprint = _input_file_fingerprint(path)
            fingerprints.append(
                {
                    "path": logical_path,
                    **fingerprint,
                }
            )
        return fingerprints

    def _param_spec_manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "kind": spec.kind,
                "options": _json_ready(spec.options),
                "low": spec.low,
                "high": spec.high,
            }
            for spec in self.param_specs
        ]

    def prepare_campaign(
        self,
        raw_samples: pd.DataFrame,
        *,
        n: int,
        seed: int,
    ) -> dict[str, Any]:
        """Freeze the LHS design and provenance before evaluating sample zero."""
        self.unc_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.unc_dir / f"run_manifest_{self.slug}_improved_fast.json"
        existing_manifest = _load_json(manifest_path) if manifest_path.exists() else None
        design = raw_samples.copy()
        design.insert(0, "sample_idx", np.arange(len(design), dtype=int))
        design_path = self.unc_dir / f"lhs_design_{self.slug}_improved_fast.csv"
        design_text = design.to_csv(index=False, float_format="%.17g", lineterminator="\n")
        desired_hash = hashlib.sha256(design_text.encode("utf-8")).hexdigest()

        if design_path.exists():
            existing_hash = _sha256_file(design_path)
            if existing_hash != desired_hash:
                raise RuntimeError(
                    f"[{self.slug}] Existing LHS design differs from N={n}, seed={seed}. "
                    f"Use a new NB09_CAMPAIGN_ID; refusing to overwrite {design_path}."
                )
        else:
            _atomic_write_text(design_path, design_text)

        provenance = {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "campaign_id": os.environ.get("NB09_CAMPAIGN_ID", "legacy_unisolated"),
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "city": self.city,
            "slug": self.slug,
            "n_samples": int(n),
            "seed": int(seed),
            "lhs_scope": getattr(self, "_lhs_scope", "full"),
            "lhs_scope_family": getattr(self, "_lhs_scope_family", None),
            "lhs_design_sha256": desired_hash,
            "parameter_specs": self._param_spec_manifest(),
            "git": self._git_provenance(),
            "input_fingerprints": self.input_fingerprints(
                existing_manifest.get("input_fingerprints", []) if existing_manifest else None
            ),
            "runtime": {
                "python": sys.version,
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
        }
        signature_payload = {
            key: provenance[key]
            for key in (
                "output_schema_version",
                "campaign_id",
                "slug",
                "n_samples",
                "seed",
                "lhs_scope",
                "lhs_scope_family",
                "lhs_design_sha256",
                "parameter_specs",
                "git",
                "input_fingerprints",
            )
        }
        provenance["campaign_signature"] = _sha256_json(signature_payload)
        if existing_manifest is not None:
            if existing_manifest.get("campaign_signature") != provenance["campaign_signature"]:
                raise RuntimeError(
                    f"[{self.slug}] Existing campaign provenance differs from the current code, inputs, "
                    f"N or seed. Use a new NB09_CAMPAIGN_ID; refusing to mix runs in {self.unc_dir}."
                )
            provenance = existing_manifest
        else:
            _atomic_write_json(manifest_path, provenance)

        self.run_provenance = provenance
        self.lhs_design_path = design_path
        self.run_manifest_path = manifest_path
        self.checkpoint_dir = self.unc_dir / "checkpoints" / "samples"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return provenance

    def get_penetration(self, ssp: int, year: int) -> float:
        ssp = int(ssp)
        year = int(year)
        if (ssp, year) in self.pen_lookup:
            return float(self.pen_lookup[(ssp, year)])
        if (2, year) in self.pen_lookup:
            return float(self.pen_lookup[(2, year)])
        ys_ssp = sorted(y for (s, y) in self.pen_lookup if s == ssp)
        if ys_ssp:
            nearest = min(ys_ssp, key=lambda yy: abs(yy - year))
            return float(self.pen_lookup[(ssp, nearest)])
        ys_2 = sorted(y for (s, y) in self.pen_lookup if s == 2)
        if ys_2:
            nearest = min(ys_2, key=lambda yy: abs(yy - year))
            return float(self.pen_lookup[(2, nearest)])
        return 0.0

    def get_kwh_per_user(self, ssp: int, year: int) -> float:
        ssp = int(ssp)
        year = int(year)
        if (ssp, year) in self.kwh_lookup:
            return float(self.kwh_lookup[(ssp, year)])
        if (2, year) in self.kwh_lookup:
            return float(self.kwh_lookup[(2, year)])
        ys_ssp = sorted(y for (s, y) in self.kwh_lookup if s == ssp)
        if ys_ssp:
            nearest = min(ys_ssp, key=lambda yy: abs(yy - year))
            return float(self.kwh_lookup[(ssp, nearest)])
        ys_2 = sorted(y for (s, y) in self.kwh_lookup if s == 2)
        if ys_2:
            nearest = min(ys_2, key=lambda yy: abs(yy - year))
            return float(self.kwh_lookup[(2, nearest)])
        return 0.0

    def dT_night_from_penetration(self, pen: float, case: str) -> float:
        pen_points = np.array(sorted(self.wh_lut.keys()), dtype=float)
        dT_points = np.array([float(self.wh_lut[p][case]) for p in pen_points], dtype=float)
        return float(np.interp(np.clip(pen, 0.0, 1.0), pen_points, dT_points))

    def cop_amplification_factor(self, dT_night: float, cop_sens: float) -> float:
        alpha = float(cop_sens) * float(dT_night) * (1.0 + 1.0 / self.cop_ref) / self.cop_ref
        return 1.0 + alpha

    def waste_heat_activity_share(self, citymean_daily: np.ndarray) -> float:
        if self.wh_activation_method != "kou_cdd_share":
            return 1.0
        arr = np.asarray(citymean_daily, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return 0.0
        denom = float(self.wh_t_full_c - self.wh_t_on_c)
        if denom <= 0:
            return float(np.mean((arr > self.wh_t_on_c).astype(float)))
        activity = np.clip((arr - self.wh_t_on_c) / denom, 0.0, 1.0)
        return float(activity.mean())

    def interpolate_coverage_pattern(self, year: int, mode: str = "base") -> np.ndarray:
        year = int(year)
        mode_key = "policy" if str(mode).lower() == "policy" else "base"
        pattern_map = self.coverage_pattern_by_mode_year[mode_key]
        if year in pattern_map:
            return pattern_map[year]
        anchor_years = np.array(sorted(pattern_map.keys()), dtype=int)
        stack = np.vstack([pattern_map[y][None, :] for y in anchor_years]).astype(np.float32)
        out = np.empty(self.n_city, dtype=np.float32)
        for idx in range(self.n_city):
            out[idx] = np.interp(year, anchor_years, stack[:, idx])
        return out

    def interpolate_coverage_raw(self, year: int, mode: str = "base") -> np.ndarray:
        """Interpolate the canonical NB05 coverage maps without re-calibration."""
        year = int(year)
        mode_key = "policy" if str(mode).lower() == "policy" else "base"
        raw_map = self.coverage_raw_by_mode_year[mode_key]
        if year in raw_map:
            return raw_map[year].copy()
        anchor_years = np.array(sorted(raw_map), dtype=int)
        stack = np.vstack([raw_map[y][None, :] for y in anchor_years]).astype(np.float32)
        out = np.empty(stack.shape[1], dtype=np.float32)
        for idx in range(stack.shape[1]):
            out[idx] = np.interp(year, anchor_years, stack[:, idx])
        return out

    def interpolate_coverage_mean(self, year: int, mode: str = "base") -> float:
        year = int(year)
        mode_key = "policy" if str(mode).lower() == "policy" else "base"
        mean_map = self.coverage_mean_by_mode_year[mode_key]
        if year in mean_map:
            return float(mean_map[year])
        anchor_years = np.array(sorted(mean_map.keys()), dtype=int)
        vals = np.array([mean_map[y] for y in anchor_years], dtype=float)
        return float(np.interp(year, anchor_years.astype(float), vals))

    def coverage_mean_for_mode(self, year: int, ac_ssp: int, mode: str = "base") -> float:
        base_mean = float(self.get_penetration(ac_ssp, year))
        if not np.isfinite(base_mean):
            base_mean = float(self.interpolate_coverage_mean(year, "base"))
        if not np.isfinite(base_mean):
            base_mean = 0.0
        if str(mode).lower() != "policy":
            return base_mean
        delta_mean = self.interpolate_coverage_mean(year, "policy") - self.interpolate_coverage_mean(year, "base")
        if not np.isfinite(delta_mean):
            delta_mean = 0.0
        return float(np.clip(base_mean + float(delta_mean), 0.0, 0.98))

    def coverage_for_sample(self, year: int, ac_ssp: int, mode: str = "base") -> np.ndarray:
        # NB05's stored maps are the canonical spatial allocation for the
        # configured AC SSP.  Returning them unchanged is essential for the
        # central NB09 point to reproduce the deterministic workflow.
        if int(ac_ssp) == self.ac_ssp_base:
            return self.interpolate_coverage_raw(year, mode=mode)
        pattern = self.interpolate_coverage_pattern(year, mode=mode)
        target_mean = self.coverage_mean_for_mode(year, ac_ssp, mode=mode)
        return _scale_pattern_to_mean_masked(pattern, self.row_is_city, target_mean, upper=0.98)

    def coverage_full_for_sample(self, year: int, ac_ssp: int, mode: str = "base") -> np.ndarray:
        """Return raw coverage indexed by the full hazard-centroid numbering."""
        full = np.full(self.hgt * self.wdt, np.nan, dtype=np.float32)
        full[self.row_cols] = self.coverage_for_sample(year, ac_ssp, mode=mode)
        return full

    def coverage_full_for_exposure(
        self,
        path: Path,
        year: int,
        ac_ssp: int,
        mode: str = "base",
    ) -> np.ndarray:
        """Return NB05-compatible coverage for mortality on an exposure grid.

        For the configured AC SSP, this exactly reproduces the gap treatment
        used by the deterministic Notebook 05/06 mortality calculations:
        missing coverage cells contribute zero to the population-weighted
        city mean, and are then filled with that (therefore diluted) mean.

        For an alternative sampled AC SSP, the same canonical spatial pattern
        and gap treatment are retained and the resulting populated-cell map is
        rescaled to the sampled population-weighted penetration target.  This
        keeps the sampled SSP interpretation tied to people rather than to an
        unweighted raster-cell average.
        """
        exp = self.load_exposure_cached(path)
        centroids = exp.gdf["centr_T2M"].to_numpy(dtype=int)
        values = exp.gdf["value"].to_numpy(dtype=float)
        n_full = self.hgt * self.wdt
        if np.any((centroids < 0) | (centroids >= n_full)):
            raise IndexError("Exposure-to-hazard centroid index is outside the AC coverage grid.")

        pop_by_centroid = np.bincount(
            centroids,
            weights=np.nan_to_num(values, nan=0.0),
            minlength=n_full,
        ).astype(float)
        populated = np.isfinite(pop_by_centroid) & (pop_by_centroid > 0)

        # Begin with the canonical NB05 map even for alternative sampled SSPs;
        # the latter are rescaled only after applying the deterministic gap
        # rule below.
        coverage = self.coverage_full_for_sample(year, self.ac_ssp_base, mode=mode).astype(float)
        if np.any(populated & ~np.isfinite(coverage)):
            numerator = float(
                np.sum(np.nan_to_num(coverage[populated], nan=0.0) * pop_by_centroid[populated])
            )
            denominator = float(np.sum(pop_by_centroid[populated]))
            fallback = numerator / denominator if denominator > 0 else 0.0
            coverage[populated & ~np.isfinite(coverage)] = fallback

        coverage[~np.isfinite(coverage)] = 0.0
        coverage = np.clip(coverage, 0.0, 0.98)

        if int(ac_ssp) != self.ac_ssp_base:
            target_mean = self.coverage_mean_for_mode(year, ac_ssp, mode=mode)
            coverage = _scale_pattern_to_weighted_mean(
                coverage,
                pop_by_centroid,
                target_mean,
                upper=0.98,
            ).astype(float)

        return coverage.astype(np.float32)

    def tree_dt2m_for_day(
        self,
        t2m_row: np.ndarray,
        month: int,
        tree_coeff_scale: float,
        tree_cap_uplift: float,
    ) -> np.ndarray:
        """Canonical day-specific NB07 dGVI->dLST->dT2M bridge."""
        cap_ratio = float(tree_cap_uplift) / max(self.tree_base_cap, 1e-6)
        dlst = self.tree_dlst_month_maps[int(month) - 1] * float(tree_coeff_scale) * cap_ratio
        return _translate_dlst_to_dt2m_quadratic(
            t2m=np.asarray(t2m_row, dtype=np.float32),
            dlst=dlst,
            lcz=self.tree_lcz,
            gvi_0_1=self.tree_gvi_0_1,
            scope=self.tree_scope,
            emulator=self.tree_emulator,
        )

    def build_muni_ac_cost_frame(
        self,
        sample: dict[str, Any],
        years_all: np.ndarray,
        pop_25y: np.ndarray,
    ) -> pd.DataFrame | None:
        if self.elec_fb_cov_yearly is None or self.elec_fb_ac_summary is None:
            return None

        cov_src = self.elec_fb_cov_yearly.copy()
        kwh_src = self.elec_fb_ac_summary.copy()
        if "muni_id" not in cov_src.columns or "muni_id" not in kwh_src.columns:
            return None

        cov_src = cov_src.loc[cov_src["muni_id"].astype(int) > 0].copy()
        kwh_src = kwh_src.loc[kwh_src["muni_id"].astype(int) > 0].copy()
        if cov_src.empty or kwh_src.empty:
            return None

        years_all = np.asarray(years_all, dtype=int)
        pop_25y = np.asarray(pop_25y, dtype=float)
        if years_all.size == 0 or pop_25y.size != years_all.size or pop_25y[0] <= 0:
            return None

        pop_scale_t = pop_25y / max(pop_25y[0], 1e-9)

        cov_rows: list[dict[str, float]] = []
        for muni_id, g in cov_src.groupby("muni_id"):
            g = g.sort_values("year")
            known_years = g["year"].to_numpy(int)
            known_base = g["ac_base_muni"].to_numpy(float)
            known_pol = g["ac_policy_muni"].to_numpy(float)
            pop_2020 = float(g["pop_muni"].iloc[0])

            base_raw_t = np.interp(years_all, known_years, known_base)
            pol_raw_t = np.interp(years_all, known_years, known_pol)
            base_raw_t[years_all <= known_years[0]] = known_base[0]
            base_raw_t[years_all >= known_years[-1]] = known_base[-1]
            pol_raw_t[years_all <= known_years[0]] = known_pol[0]
            pol_raw_t[years_all >= known_years[-1]] = known_pol[-1]

            for year, base_raw, pol_raw, pop_scale in zip(years_all, base_raw_t, pol_raw_t, pop_scale_t):
                cov_rows.append(
                    {
                        "year": int(year),
                        "muni_id": int(muni_id),
                        "pop_muni": float(pop_2020 * pop_scale),
                        "base_share_raw": float(np.clip(base_raw, 0.0, 0.98)),
                        "policy_share_raw": float(np.clip(pol_raw, 0.0, 0.98)),
                    }
                )

        kwh_rows: list[dict[str, float]] = []
        for muni_id, g in kwh_src.groupby("muni_id"):
            g = g.sort_values("year")
            known_years = g["year"].to_numpy(int)
            vals = g["kwh_per_user_muni"].to_numpy(float)
            kwh_interp = np.interp(years_all, known_years, vals)
            kwh_interp[years_all <= known_years[0]] = vals[0]
            kwh_interp[years_all >= known_years[-1]] = vals[-1]
            for year, value in zip(years_all, kwh_interp):
                kwh_rows.append(
                    {
                        "year": int(year),
                        "muni_id": int(muni_id),
                        "kwh_per_user_raw": float(max(value, 0.0)),
                    }
                )

        cov_yearly = pd.DataFrame(cov_rows)
        muni_kwh_full = pd.DataFrame(kwh_rows)
        cov_yearly = cov_yearly.merge(muni_kwh_full, on=["year", "muni_id"], how="left")
        cov_yearly["kwh_per_user_raw"] = cov_yearly["kwh_per_user_raw"].fillna(0.0)

        if self.elec_fb_dgvi_by_region:
            cov_yearly["dGVI"] = cov_yearly["muni_id"].map(self.elec_fb_dgvi_by_region).fillna(0.0)
        else:
            cov_yearly["dGVI"] = 0.0

        for year in years_all:
            mask = cov_yearly["year"] == int(year)
            weights = cov_yearly.loc[mask, "pop_muni"].to_numpy(float)
            if int(sample["ac_ssp"]) == self.ac_ssp_base:
                # Exact NB08 central trajectories: interpolate the NB05
                # municipality tables and do not recalibrate them to a second
                # NUTS-level target.
                cov_yearly.loc[mask, "base_share_t"] = cov_yearly.loc[mask, "base_share_raw"].to_numpy(float)
                cov_yearly.loc[mask, "policy_share_t"] = cov_yearly.loc[mask, "policy_share_raw"].to_numpy(float)
                cov_yearly.loc[mask, "kwh_per_user_t"] = cov_yearly.loc[mask, "kwh_per_user_raw"].to_numpy(float)
            else:
                base_target = self.coverage_mean_for_mode(int(year), sample["ac_ssp"], mode="base")
                policy_target = self.coverage_mean_for_mode(int(year), sample["ac_ssp"], mode="policy")

                cov_yearly.loc[mask, "base_share_t"] = _scale_pattern_to_weighted_mean(
                    cov_yearly.loc[mask, "base_share_raw"].to_numpy(float),
                    weights,
                    base_target,
                    upper=0.98,
                )
                cov_yearly.loc[mask, "policy_share_t"] = _scale_pattern_to_weighted_mean(
                    cov_yearly.loc[mask, "policy_share_raw"].to_numpy(float),
                    weights,
                    policy_target,
                    upper=0.98,
                )

                user_weights = cov_yearly.loc[mask, "pop_muni"].to_numpy(float) * np.maximum(
                    cov_yearly.loc[mask, "base_share_t"].to_numpy(float),
                    1e-9,
                )
                target_kwh = self.get_kwh_per_user(sample["ac_ssp"], int(year))
                cov_yearly.loc[mask, "kwh_per_user_t"] = _scale_pattern_to_weighted_mean(
                    cov_yearly.loc[mask, "kwh_per_user_raw"].to_numpy(float),
                    user_weights,
                    target_kwh,
                    upper=None,
                )

        cov_yearly["base_share_t"] = cov_yearly["base_share_t"].astype(float).clip(0.0, 0.98)
        cov_yearly["policy_share_t"] = cov_yearly["policy_share_t"].astype(float).clip(0.0, 0.98)
        cov_yearly["policy_share_t"] = np.maximum(cov_yearly["policy_share_t"], cov_yearly["base_share_t"])
        cov_yearly["dshare_t"] = (cov_yearly["policy_share_t"] - cov_yearly["base_share_t"]).clip(0.0, 1.0)
        cov_yearly["kwh_per_user_t"] = cov_yearly["kwh_per_user_t"].astype(float).clip(lower=0.0)
        return cov_yearly

    def load_exposure_cached(self, path: Path) -> Exposures:
        key = str(path)
        if key not in self.exp_cache:
            try:
                exp = Exposures.from_hdf5(key)
            except Exception:
                from shapely import wkb

                df = pd.read_hdf(key, "exposures")
                geom = df["geometry"].apply(lambda b: wkb.loads(b) if isinstance(b, (bytes, bytearray)) else b)
                gdf = gpd.GeoDataFrame(df.drop(columns=["geometry"]), geometry=geom, crs="EPSG:4326")
                exp = Exposures(gdf)
            if f"centr_T2M" not in exp.gdf.columns:
                # threshold=None: assign each exposure cell to its coincident hazard centroid, matching the
                # deterministic ImpactCalc (nb04). threshold=0 on the full multi-age exposure (triplicate
                # geometries) kept only ~1/3 of cells, silently dropping ~2/3 of the population from the
                # mortality impact (~3x too low); verified threshold=None recovers 99% of the deterministic.
                exp.assign_centroids(self.hazard_template, distance="euclidean", threshold=None, overwrite=True)
            self.exp_cache[key] = exp
        return self.exp_cache[key]

    def load_exposure_age(self, path: Path, age_label: str) -> Exposures:
        key = (str(path), age_label)
        if key not in self.exp_age_cache:
            exp = self.load_exposure_cached(path)
            gdf = exp.gdf[exp.gdf["age_group"].astype(str) == str(age_label)].copy()
            self.exp_age_cache[key] = Exposures(gdf)
        return self.exp_age_cache[key]

    def exposure_path_for_year(self, year: int, exp_ssp_idx: int) -> Path:
        if year in self.direct_years:
            return self.exp_paths[(None, year)]
        ssp = self.exp_ssp_options[int(exp_ssp_idx)]
        return self.exp_paths[(ssp, year)]

    def _manifest_npz_path(self, value: Any) -> Path:
        """Resolve an NB03 manifest NPZ after a repository/host move."""
        path = Path(str(value))
        candidates = [path, self.int_dir / path.name, self.out / path.name]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"Could not resolve population NPZ {value!r}; checked: "
            + ", ".join(str(candidate) for candidate in candidates)
        )

    def population_total_for_year(self, year: int, exp_ssp_idx: int) -> float:
        """Return the NB03 population-grid total used by NB06 for one anchor."""
        year = int(year)
        baseline = self.expo_manifest.get("baseline", {}) or {}
        baseline_year = int(baseline.get("year", min(self.years)))
        if year == baseline_year and baseline.get("pop_npz"):
            scenario: str | None = None
            record = baseline
        elif year in self.direct_years:
            scenario = None
            record = (self.expo_manifest.get("direct_worldpop", {}) or {}).get(str(year))
        else:
            scenario = self.exp_ssp_options[int(exp_ssp_idx)]
            record = (
                (self.expo_manifest.get("scenarios", {}) or {})
                .get(scenario, {})
                .get(str(year))
            )
        if not isinstance(record, dict) or not record.get("pop_npz"):
            raise KeyError(
                f"No pop_npz manifest record for year={year}, scenario={scenario!r}."
            )
        key = (scenario, year)
        if key not in self.population_total_cache:
            pop_path = self._manifest_npz_path(record["pop_npz"])
            self.population_total_cache[key] = float(np.nansum(np.load(pop_path)["pop"]))
        return self.population_total_cache[key]

    def ac_cba_population_series(self, years: np.ndarray, sample: dict[str, Any]) -> np.ndarray:
        """Reproduce Notebook 08's population path for AC expenditure.

        NB08 combines the 2020 direct/baseline population with the selected
        SSP records and interpolates between those points.  In the current
        NB03 manifests, SSP records begin in 2040, so the direct 2030
        WorldPop observation is deliberately not inserted into this AC-cost
        path.  NB06 uses all four modeled anchors instead; that separate path
        remains in ``evaluate_branch_anchors`` for EWS.
        """
        target_years = np.asarray(years, dtype=int)
        if target_years.size == 0:
            return np.array([], dtype=float)

        baseline = self.expo_manifest.get("baseline", {}) or {}
        baseline_year = int(baseline.get("year", min(self.years)))
        direct = self.expo_manifest.get("direct_worldpop", {}) or {}
        baseline_record = baseline if baseline.get("pop_npz") else direct.get(str(baseline_year))
        if not isinstance(baseline_record, dict) or not baseline_record.get("pop_npz"):
            raise KeyError(f"No baseline/direct pop_npz record for AC CBA year {baseline_year}.")
        baseline_key = (None, baseline_year)
        if baseline_key not in self.population_total_cache:
            self.population_total_cache[baseline_key] = float(
                np.nansum(np.load(self._manifest_npz_path(baseline_record["pop_npz"]))["pop"])
            )
        baseline_total = self.population_total_cache[baseline_key]

        scenario = self.exp_ssp_options[int(sample["EXP_SSP_IDX"])]
        scenario_records = (
            (self.expo_manifest.get("scenarios", {}) or {}).get(scenario, {}) or {}
        )
        scenario_years = sorted(
            int(year)
            for year, record in scenario_records.items()
            if int(year) != baseline_year and isinstance(record, dict) and record.get("pop_npz")
        )
        anchor_years = [baseline_year]
        anchor_totals = [baseline_total]
        for year in scenario_years:
            record = scenario_records[str(year)]
            anchor_years.append(year)
            key = (scenario, year)
            if key not in self.population_total_cache:
                self.population_total_cache[key] = float(
                    np.nansum(np.load(self._manifest_npz_path(record["pop_npz"]))["pop"])
                )
            anchor_totals.append(self.population_total_cache[key])
        scale = float(sample.get("EXP_TOTAL_SCALE", 1.0))
        return scale * np.interp(
            target_years.astype(float),
            np.asarray(anchor_years, dtype=float),
            np.asarray(anchor_totals, dtype=float),
        )

    def load_if_block(self, family: str, year: int) -> dict[str, Any]:
        key = (family, int(year))
        if key not in self.if_block_cache:
            d = _load_json(self.if_jsons[family])
            y = str(int(year))
            self.if_block_cache[key] = d["ifs_by_year"][y] if "ifs_by_year" in d else d
        return self.if_block_cache[key]

    def build_if_set_ac_only(
        self,
        family: str,
        year: int,
        tref_c: float,
        mdd_scale_lt15: float,
        mdd_scale_15_64: float,
        mdd_scale_65p: float,
        disp_frac: float,
        paa_scale: float,
        ac_ssp: int,
        ac_eff_scen: str,
        ac_mode: str = "base",
    ) -> ImpactFuncSet:
        """Build the sampled IF set before spatial AC attenuation.

        AC cannot be represented by multiplying a city-level IF by mean
        coverage: NB05 applies ``1 - efficacy_age * coverage_cell`` at each
        exposure cell.  The spatial attenuation is therefore applied to the
        exposure values in :meth:`evaluate_year`; this method only applies the
        non-spatial IF uncertainty dimensions.
        """
        block = self.load_if_block(family, year)
        funcs: list[ImpactFunc] = []
        for age in AGE_ORDER:
            rec = block[age]
            intensity = np.asarray(rec["intensity"], dtype=float)
            if family not in ("masselot", "masselot_tail"):
                intensity = intensity + (float(tref_c) - TREF_BASE)
            if self.use_extreme_track and np.isfinite(self.extreme_tstar_c):
                # Track-B hazards are event-day exceedances above T*.
                intensity = intensity - float(self.extreme_tstar_c)
            mdd = np.asarray(rec["mdd"], dtype=float)
            paa = np.asarray(rec.get("paa", np.ones_like(mdd)), dtype=float)
            age_scale = {"<15": mdd_scale_lt15, "15-64": mdd_scale_15_64, "65+": mdd_scale_65p}[age]
            mdd = mdd * float(age_scale) * (1.0 - float(disp_frac))
            mdd = np.clip(mdd, 0.0, 1.0)
            paa = np.clip(paa * float(paa_scale), 0.0, 1.0)
            funcs.append(
                ImpactFunc(
                    haz_type="T2M",
                    id=AGE_TO_ID[age],
                    intensity=intensity,
                    mdd=mdd,
                    paa=paa,
                    intensity_unit="degC exceedance" if self.use_extreme_track else "degC",
                    name=f"{family}_{age}_{year}_ac_only",
                )
            )
        return ImpactFuncSet(funcs)

    def build_hazard(
        self,
        year: int,
        baseline_mode: str,
        clim_scen: str,
        clim_band: str,
        clim_source: str,
        gcm_model: str,
        ac_ssp: int,
        wh_case: str,
        wh_ratio: float,
        cop_case: str,
        wh_enabled: bool,
        cop_enabled: bool,
        tree_coeff_scale: float,
        tree_cap_uplift: float,
        tree_ramp_years: int,
        tree_start_age: int,
        ac_mode: str = "base",
        wh_mode: str | None = None,
        tree_enabled: bool = True,
        temperature_offset_c: float = 0.0,
        extreme_threshold_c: float | None = None,
        extreme_min_duration_days: int | None = None,
    ) -> Hazard:
        months = self.months_by_year[year]
        n_days = len(months)
        base = self.base_matrix_by_year[year].copy()
        data = base.data
        indptr = base.indptr

        mode_adj = self.mode_day_anom.get((baseline_mode, year), np.zeros(n_days, dtype=np.float32))
        if str(clim_source) == "gcm_model" and str(gcm_model) != "__none__":
            clim_adj = self.clim_day_anom_gcm.get((str(clim_scen), str(gcm_model), year), np.zeros(n_days, dtype=np.float32))
        else:
            clim_adj = self.clim_day_anom_bands.get((str(clim_scen), str(clim_band).lower(), year), np.zeros(n_days, dtype=np.float32))
        for row in range(n_days):
            a, b = indptr[row], indptr[row + 1]
            data[a:b] += float(mode_adj[row] + clim_adj[row] + temperature_offset_c)
            if tree_enabled:
                # NB07 translates the monthly dLST map at each day's actual
                # baseline T2M.  Tree maturity is applied later to the annual
                # benefit stream through the cohort rollout convolution.
                data[a:b] += self.tree_dt2m_for_day(
                    data[a:b],
                    int(months[row]),
                    tree_coeff_scale,
                    tree_cap_uplift,
                )

        if self.use_extreme_track:
            tstar = float(self.extreme_tstar_c if extreme_threshold_c is None else extreme_threshold_c)
            if not np.isfinite(tstar):
                raise RuntimeError(f"Extreme track active for {self.slug} but threshold T* is missing.")
            min_dur = max(1, int(self.extreme_min_duration_days if extreme_min_duration_days is None else extreme_min_duration_days))

            den = float(self.mask_vec.sum())
            if den <= 0:
                raise RuntimeError("City mask has no active cells for extreme-hazard conversion in NB09 improved.")

            weighted_num = np.asarray(base.multiply(self.mask_vec.reshape(1, -1)).sum(axis=1)).ravel().astype(float)
            citymean = weighted_num / den
            season_mask = _season_mask_by_md(
                self.dates_by_year[year], self.extreme_season_start_md, self.extreme_season_end_md
            )
            is_hot = season_mask & (citymean > tstar)
            grp = np.cumsum(np.r_[True, is_hot[1:] != is_hot[:-1]])
            run_len = pd.Series(is_hot).groupby(grp).transform("size").to_numpy(dtype=int)
            is_event = is_hot & (run_len >= min_dur)

            base.data = np.clip(base.data - tstar, 0.0, None).astype(np.float32)
            row_ids = np.repeat(np.arange(base.shape[0], dtype=int), np.diff(base.indptr))
            base.data *= is_event[row_ids].astype(np.float32)
            base.eliminate_zeros()

        h = Hazard("T2M")
        h.haz_type = "T2M"
        h.units = "degC exceedance above T*" if self.use_extreme_track else "degC (daily mean)"
        h.centroids = self.centroids
        h.intensity = base
        h.fraction = sparse.csr_matrix(np.tile(self.mask_vec, (n_days, 1)))
        h.date = self.dates_by_year[year]
        h.event_id = np.array([int(pd.Timestamp(d).strftime("%Y%m%d")) for d in h.date], dtype=int)
        h.event_name = np.array([f"T2M_{str(pd.Timestamp(d).date())}" for d in h.date], dtype=object)
        h.frequency = np.full(n_days, 1.0 / self.days_in_year(year), dtype=float)
        h.frequency_unit = "1/year"
        if self.use_extreme_track:
            h.orig = np.array([f"{self.city}_NB09_UQ_IMPROVED_EXTREME"] * n_days, dtype=object)
        else:
            h.orig = np.array([f"{self.city}_NB09_UQ_IMPROVED"] * n_days, dtype=object)
        return h

    def tree_maturity_factor(self, year: int, ramp_years: int, start_age: int) -> float:
        t = int(year) - min(self.years)
        if t <= 0:
            return 0.0
        return float(np.clip((t + float(start_age)) / max(float(ramp_years), 1.0), 0.0, 1.0))

    def days_in_year(self, year: int) -> int:
        ts = pd.Timestamp(year=int(year), month=12, day=31)
        return 366 if ts.is_leap_year else 365

    def threshold_for_year(self, year: int, threshold_ref: float, recalib_interval: int, pop_totals: dict[int, float]) -> float:
        if recalib_interval <= 0:
            return float(threshold_ref)
        anchor_years = np.array(sorted(pop_totals.keys()), dtype=int)
        anchor_pops = np.array([float(pop_totals[y]) for y in anchor_years], dtype=float)
        pop_ref = float(np.interp(self.ews_threshold_ref_year, anchor_years.astype(float), anchor_pops))
        if pop_ref <= 0:
            return float(threshold_ref)
        calib_year = self.ews_threshold_ref_year + ((int(year) - self.ews_threshold_ref_year) // int(recalib_interval)) * int(recalib_interval)
        calib_year = max(self.ews_threshold_ref_year, calib_year)
        pop_calib = float(np.interp(calib_year, anchor_years.astype(float), anchor_pops))
        return float(threshold_ref) * (pop_calib / pop_ref)

    def ews_uses_event_mask_warning(self) -> bool:
        return bool(self.use_extreme_track and self.ews_warning_trigger_mode == "event_mask")

    def extreme_threshold_from_percentile(self, baseline_mode: str, percentile: float) -> float:
        """
        Track-B event threshold derived from reference-year city-mean T2M in-season.
        The resulting absolute threshold is fixed within a sample across all years.
        """
        ref_year = self.ews_threshold_ref_year if self.ews_threshold_ref_year in self.years else min(self.years)
        mode_adj = self.mode_day_anom.get((baseline_mode, ref_year), np.zeros(len(self.dates_by_year[ref_year]), dtype=np.float32))
        citymean_ref = self.ref_citymean_by_year[ref_year] + mode_adj
        season_mask = _season_mask_by_md(self.dates_by_year[ref_year], self.extreme_season_start_md, self.extreme_season_end_md)
        vals = np.asarray(citymean_ref[season_mask], dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            return float(self.extreme_tstar_c)
        pct = float(np.clip(percentile, 0.0, 100.0))
        return float(np.nanpercentile(vals, pct))

    def ramp_factor(self, year: int, ews_ramp_years: int) -> float:
        dt_years = max(int(year) - min(self.years), 0)
        if ews_ramp_years <= 0:
            return 1.0
        # Canonical NB06 uses a step interpretation: initial efficacy for the
        # first ``ramp_years`` policy years, then full configured efficacy.
        return float(np.clip(self.ews_init if dt_years < int(ews_ramp_years) else 1.0, 0.0, 1.0))

    def pop_total_for_exposure(self, path: Path, scale: float) -> float:
        exp = self.load_exposure_cached(path)
        return float(exp.gdf["value"].astype(float).sum() * float(scale))

    def coverage_mean_for_exposure(
        self,
        path: Path,
        year: int,
        ac_ssp: int,
        ac_mode: str,
    ) -> float:
        """Population-weighted AC penetration on the modeled exposure cells."""
        exp = self.load_exposure_cached(path)
        coverage_full = self.coverage_full_for_exposure(path, year, ac_ssp, mode=ac_mode)
        centroids = exp.gdf["centr_T2M"].to_numpy(dtype=int)
        if np.any((centroids < 0) | (centroids >= coverage_full.size)):
            raise IndexError("Exposure-to-hazard centroid index is outside the AC coverage grid.")
        return _weighted_mean(coverage_full[centroids], exp.gdf["value"].to_numpy(float))

    def coverage_mean_for_ews(self, year: int, ac_ssp: int, mode: str = "base") -> float:
        """Return the unweighted city-pixel coverage mean used by NB06."""
        coverage = self.coverage_for_sample(year, ac_ssp, mode=mode)
        values = np.asarray(coverage, dtype=float)[self.row_is_city]
        finite = values[np.isfinite(values)]
        return float(finite.mean()) if finite.size else 0.0

    def coverage_mean_for_waste_heat(self, year: int, ac_ssp: int, mode: str = "base") -> float:
        """Return the population-weighted municipal penetration used by NB05."""
        return float(
            self.coverage_series_for_waste_heat(
                np.asarray([int(year)], dtype=int),
                ac_ssp,
                mode=mode,
            )[0]
        )

    def coverage_series_for_waste_heat(
        self,
        years: np.ndarray,
        ac_ssp: int,
        mode: str = "base",
    ) -> np.ndarray:
        """Return the NB05 citywide AC-penetration path.

        Notebook 05 first interpolates municipal users and total population
        from the anchor years and only then divides the two series.  Interpolating
        anchor-year penetration ratios directly is close, but not algebraically
        identical when population changes.  The distinction matters for the
        central NB01--NB08 parity control.
        """
        target_years = np.asarray(years, dtype=float)
        if int(ac_ssp) == self.ac_ssp_base and self.ac_muni_cov_yearly is not None:
            df = self.ac_muni_cov_yearly
            coverage_col = "ac_policy_muni" if str(mode).lower() == "policy" else "ac_base_muni"
            users_anchors: dict[int, float] = {}
            pop_anchors: dict[int, float] = {}
            if coverage_col in df.columns and {"year", "pop_muni"}.issubset(df.columns):
                for anchor_year, group in df.groupby("year"):
                    pop = pd.to_numeric(group["pop_muni"], errors="coerce").to_numpy(float)
                    cov = pd.to_numeric(group[coverage_col], errors="coerce").to_numpy(float)
                    valid = np.isfinite(pop) & np.isfinite(cov) & (pop >= 0)
                    if np.any(valid) and float(pop[valid].sum()) > 0:
                        users_anchors[int(anchor_year)] = float(np.sum(pop[valid] * cov[valid]))
                        pop_anchors[int(anchor_year)] = float(np.sum(pop[valid]))
            common_years = sorted(set(users_anchors).intersection(pop_anchors))
            if common_years:
                anchor_years = np.asarray(common_years, dtype=float)
                users = np.asarray([users_anchors[int(y)] for y in anchor_years], dtype=float)
                population = np.asarray([pop_anchors[int(y)] for y in anchor_years], dtype=float)
                users_t = np.interp(target_years, anchor_years, users)
                population_t = np.interp(target_years, anchor_years, population)
                return np.divide(
                    users_t,
                    population_t,
                    out=np.zeros_like(users_t, dtype=float),
                    where=population_t > 0,
                )
        return np.asarray(
            [self.coverage_mean_for_mode(int(year), ac_ssp, mode=mode) for year in target_years],
            dtype=float,
        )

    def evaluate_year(
        self,
        year: int,
        sample: dict[str, Any],
        threshold_ref: float | None = None,
        pop_totals: dict[int, float] | None = None,
        *,
        ac_mode: str = "base",
        wh_mode: str | None = None,
        tree_enabled: bool = True,
        ews_enabled: bool = True,
        temperature_offset_c: float = 0.0,
        extreme_threshold_c: float | None = None,
        extreme_min_duration_days: int | None = None,
    ) -> dict[str, Any]:
        exp_path = self.exposure_path_for_year(year, sample["EXP_SSP_IDX"])
        scale = float(sample["EXP_TOTAL_SCALE"])
        hazard = self.build_hazard(
            year=year,
            baseline_mode=sample["baseline_mode"],
            clim_scen=sample["clim_scen"],
            clim_band=sample["clim_band"],
            clim_source=sample["clim_source"],
            gcm_model=sample["gcm_model"],
            ac_ssp=sample["ac_ssp"],
            ac_mode=ac_mode,
            wh_case=sample["wh_case"],
            wh_ratio=sample["WH_RATIO"],
            cop_case=sample["cop_case"],
            wh_enabled=sample["wh_enabled"],
            cop_enabled=sample["cop_enabled"],
            tree_coeff_scale=sample["TREE_COEFF_SCALE"],
            tree_cap_uplift=sample["TREE_CAP_UPLIFT"],
            tree_ramp_years=sample["tree_ramp_years"],
            tree_start_age=sample["tree_start_age"],
            wh_mode=wh_mode,
            tree_enabled=tree_enabled,
            temperature_offset_c=temperature_offset_c,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration_days,
        )
        ifs = self.build_if_set_ac_only(
            family=sample["if_family"],
            year=year,
            tref_c=sample["t_ref_C"],
            mdd_scale_lt15=sample["MDD_SCALE_LT15"],
            mdd_scale_15_64=sample["MDD_SCALE_15_64"],
            mdd_scale_65p=sample["MDD_SCALE_65P"],
            disp_frac=sample["DISP_FRAC"],
            paa_scale=sample["PAA_SCALE"],
            ac_ssp=sample["ac_ssp"],
            ac_eff_scen=sample["ac_eff_scenario"],
            ac_mode=ac_mode,
        )

        # Canonical NB05 waste-heat activation is based on the masked daily
        # city-mean hazard temperature, before mortality is evaluated.  Keep
        # it with the branch result so aggregate feedback can be reconstructed
        # without injecting waste heat into the spatial hazard itself.
        mask_den = float(self.mask_vec.sum())
        if mask_den <= 0:
            raise RuntimeError("City mask has no active cells for city-mean hazard diagnostics.")
        hazard_citymean_daily = np.asarray(
            hazard.intensity.multiply(self.mask_vec.reshape(1, -1)).sum(axis=1)
        ).ravel().astype(float) / mask_den

        age_impacts: dict[str, np.ndarray] = {}
        daily_total = np.zeros(len(self.dates_by_year[year]), dtype=float)
        coverage_full = self.coverage_full_for_exposure(
            exp_path,
            year,
            sample["ac_ssp"],
            mode=ac_mode,
        )
        ac_eff_map = self.cfg["efficacy_scenarios"][sample["ac_eff_scenario"]]
        for age in AGE_ORDER:
            exp_age = copy.deepcopy(self.load_exposure_age(exp_path, age))
            centroid_idx = exp_age.gdf["centr_T2M"].to_numpy(dtype=int)
            if np.any((centroid_idx < 0) | (centroid_idx >= coverage_full.size)):
                raise IndexError("Exposure-to-hazard centroid index is outside the AC coverage grid.")
            ac_eff_age = float(ac_eff_map.get(age, ac_eff_map.get("default", 0.30)))
            ac_residual = np.clip(1.0 - ac_eff_age * coverage_full[centroid_idx], 0.0, 1.0)
            exp_age.gdf["value"] = exp_age.gdf["value"].astype(float) * scale * ac_residual
            imp = ImpactCalc(exp_age, ifs, hazard).impact(save_mat=False, assign_centroids=False)
            arr = np.asarray(imp.at_event, dtype=float)
            age_impacts[age] = arr
            daily_total += arr

        dates = self.dates_by_year[year]
        season_mask = _season_mask_by_md(dates, self.season_start_md, self.season_end_md)
        event_day_mask = np.diff(hazard.intensity.indptr) > 0 if self.use_extreme_track else np.zeros_like(daily_total, dtype=bool)

        pen = self.coverage_mean_for_exposure(exp_path, year, sample["ac_ssp"], ac_mode)
        overlap = float(self.ews_overlap.get(sample["ews_overlap_level"], self.ews_overlap.get("central", 0.3)))
        ac_penalty = float(np.clip(1.0 - overlap * pen, 0.0, 1.0))
        ramp_factor = self.ramp_factor(year, sample["ews_ramp_years"])

        if not ews_enabled:
            threshold_year = np.nan
            warning_mask = np.zeros_like(daily_total, dtype=bool)
        elif self.ews_uses_event_mask_warning():
            threshold_year = np.nan
            warning_mask = season_mask & event_day_mask
        elif threshold_ref is None:
            threshold_year = np.nan
            warning_mask = np.zeros_like(daily_total, dtype=bool)
        else:
            threshold_year = self.threshold_for_year(year, threshold_ref, sample["ews_recalib_years"], pop_totals or {})
            warning_mask = season_mask & (daily_total >= threshold_year)

        residual_total = np.zeros_like(daily_total)
        gross_by_age: dict[str, float] = {}
        net_by_age: dict[str, float] = {}
        lys_by_age: dict[str, float] = {}
        deaths_warning_by_age: dict[str, float] = {}

        for age in AGE_ORDER:
            base_arr = age_impacts[age]
            residual = base_arr.copy()
            deaths_warning = float(base_arr[warning_mask].sum())
            deaths_warning_by_age[age] = deaths_warning

            _interp = str(sample["ews_interpretation"]).lower()
            if _interp == "marginal":
                lvl = sample[f"ews_eff_{self._age_key(age)}_level"]
                eff_age = float(self.ews_marg.get(age, {}).get(lvl, self.ews_marg.get(age, {}).get("central", 0.0)))
            elif _interp == "intermediate":
                # meteo-HHWS midpoint (mirrors NB06): 50/50 mix of the age-differentiated
                # marginal efficacy and the (age-flat) counterfactual efficacy.
                lvl = sample[f"ews_eff_{self._age_key(age)}_level"]
                eff_marg = float(self.ews_marg.get(age, {}).get(lvl, self.ews_marg.get(age, {}).get("central", 0.0)))
                eff_cf = float(self.ews_cf.get(sample["ews_cf_eff_level"], self.ews_cf.get("central", 0.0)))
                eff_age = 0.5 * eff_marg + 0.5 * eff_cf
            else:
                eff_age = float(self.ews_cf.get(sample["ews_cf_eff_level"], self.ews_cf.get("central", 0.0)))

            disp_lvl = sample[f"ews_disp_{self._age_key(age)}_level"]
            disp_age = float(self.ews_disp.get(age, {}).get(disp_lvl, self.ews_disp.get(age, {}).get("central", 0.0)))
            gross_factor = eff_age * ramp_factor * ac_penalty
            net_factor = gross_factor * (1.0 - disp_age)

            gross = deaths_warning * gross_factor
            net = deaths_warning * net_factor
            lys = net * float(self.ews_rly.get(age, 10))
            if np.any(warning_mask):
                residual[warning_mask] = residual[warning_mask] * (1.0 - net_factor)

            gross_by_age[age] = gross
            net_by_age[age] = net
            lys_by_age[age] = lys
            residual_total += residual

        return {
            "year": int(year),
            "daily_base_total": daily_total,
            "daily_residual_total": residual_total,
            "age_impacts": age_impacts,
            "season_mask": season_mask,
            "event_day_mask": event_day_mask,
            "warning_mask": warning_mask,
            "warning_days": int(warning_mask.sum()),
            "threshold": float(threshold_year),
            "deaths_on_warning_days": float(daily_total[warning_mask].sum()),
            "deaths_warning_by_age": deaths_warning_by_age,
            "gross_by_age": gross_by_age,
            "net_by_age": net_by_age,
            "lys_by_age": lys_by_age,
            "gross_avoided": float(sum(gross_by_age.values())),
            "net_avoided": float(sum(net_by_age.values())),
            "life_years_saved": float(sum(lys_by_age.values())),
            "ac_penalty": ac_penalty,
            "ac_mode": str(ac_mode),
            "ramp_factor": ramp_factor,
            "ac_coverage_mean": pen,
            "hazard_citymean_daily": hazard_citymean_daily,
            # NB06 population scaling is defined from NB03's population NPZs,
            # not from the serialised exposure-table sum.
            "pop_total": self.population_total_for_year(year, sample["EXP_SSP_IDX"]) * scale,
        }

    def _apply_ews_to_base_year(
        self,
        *,
        year: int,
        sample: dict[str, Any],
        base_res: dict[str, Any],
        pop_totals: dict[int, float],
        threshold_ref: float | None,
        ac_mode: str,
    ) -> dict[str, Any]:
        daily_total = np.asarray(base_res["daily_base_total"], dtype=float)
        age_impacts = base_res["age_impacts"]
        season_mask = np.asarray(
            base_res.get("season_mask", _season_mask_by_md(self.dates_by_year[year], self.season_start_md, self.season_end_md)),
            dtype=bool,
        )
        event_day_mask = np.asarray(base_res.get("event_day_mask", np.zeros_like(daily_total, dtype=bool)), dtype=bool)

        if self.ews_uses_event_mask_warning():
            threshold_year = np.nan
            warning_mask = season_mask & event_day_mask
        elif threshold_ref is None:
            threshold_year = np.nan
            warning_mask = np.zeros_like(daily_total, dtype=bool)
        else:
            threshold_year = self.threshold_for_year(year, threshold_ref, sample["ews_recalib_years"], pop_totals)
            warning_mask = season_mask & (daily_total >= threshold_year)

        # NB06 defines the overlap term from the unweighted mean of the
        # city-pixel coverage map.  Keep that exact convention here; it is
        # distinct from NB05's population-weighted municipal penetration used
        # for waste heat and from spatial coverage applied to mortality.
        pen = self.coverage_mean_for_ews(year, sample["ac_ssp"], mode=ac_mode)
        overlap = float(self.ews_overlap.get(sample["ews_overlap_level"], self.ews_overlap.get("central", 0.3)))
        ac_penalty = float(np.clip(1.0 - overlap * pen, 0.0, 1.0))
        ramp_factor = self.ramp_factor(year, sample["ews_ramp_years"])

        residual_total = np.zeros_like(daily_total)
        gross_by_age: dict[str, float] = {}
        net_by_age: dict[str, float] = {}
        lys_by_age: dict[str, float] = {}
        deaths_warning_by_age: dict[str, float] = {}

        for age in AGE_ORDER:
            base_arr = np.asarray(age_impacts[age], dtype=float)
            residual = base_arr.copy()
            deaths_warning = float(base_arr[warning_mask].sum())
            deaths_warning_by_age[age] = deaths_warning

            _interp = str(sample["ews_interpretation"]).lower()
            if _interp == "marginal":
                lvl = sample[f"ews_eff_{self._age_key(age)}_level"]
                eff_age = float(self.ews_marg.get(age, {}).get(lvl, self.ews_marg.get(age, {}).get("central", 0.0)))
            elif _interp == "intermediate":
                # meteo-HHWS midpoint (mirrors NB06): 50/50 mix of the age-differentiated
                # marginal efficacy and the (age-flat) counterfactual efficacy.
                lvl = sample[f"ews_eff_{self._age_key(age)}_level"]
                eff_marg = float(self.ews_marg.get(age, {}).get(lvl, self.ews_marg.get(age, {}).get("central", 0.0)))
                eff_cf = float(self.ews_cf.get(sample["ews_cf_eff_level"], self.ews_cf.get("central", 0.0)))
                eff_age = 0.5 * eff_marg + 0.5 * eff_cf
            else:
                eff_age = float(self.ews_cf.get(sample["ews_cf_eff_level"], self.ews_cf.get("central", 0.0)))

            disp_lvl = sample[f"ews_disp_{self._age_key(age)}_level"]
            disp_age = float(self.ews_disp.get(age, {}).get(disp_lvl, self.ews_disp.get(age, {}).get("central", 0.0)))
            gross_factor = eff_age * ramp_factor * ac_penalty
            net_factor = gross_factor * (1.0 - disp_age)

            gross = deaths_warning * gross_factor
            net = deaths_warning * net_factor
            lys = net * float(self.ews_rly.get(age, 10))
            if np.any(warning_mask):
                residual[warning_mask] = residual[warning_mask] * (1.0 - net_factor)

            gross_by_age[age] = gross
            net_by_age[age] = net
            lys_by_age[age] = lys
            residual_total += residual

        return {
            "year": int(year),
            "daily_base_total": daily_total,
            "daily_residual_total": residual_total,
            "age_impacts": age_impacts,
            "season_mask": season_mask,
            "event_day_mask": event_day_mask,
            "warning_mask": warning_mask,
            "warning_days": int(warning_mask.sum()),
            "threshold": float(threshold_year),
            "deaths_on_warning_days": float(daily_total[warning_mask].sum()),
            "deaths_warning_by_age": deaths_warning_by_age,
            "gross_by_age": gross_by_age,
            "net_by_age": net_by_age,
            "lys_by_age": lys_by_age,
            "gross_avoided": float(sum(gross_by_age.values())),
            "net_avoided": float(sum(net_by_age.values())),
            "life_years_saved": float(sum(lys_by_age.values())),
            "ac_penalty": ac_penalty,
            "ac_mode": str(ac_mode),
            "ramp_factor": ramp_factor,
            "ac_coverage_mean": pen,
            "hazard_citymean_daily": np.asarray(base_res["hazard_citymean_daily"], dtype=float),
            "pop_total": float(base_res.get("pop_total", 0.0)),
        }

    def _apply_ews_to_base_anchors(
        self,
        *,
        sample: dict[str, Any],
        base_anchor_results: dict[int, dict[str, Any]],
        base_pop_totals: dict[int, float],
        ac_mode: str,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, float], float | None]:
        if self.ews_uses_event_mask_warning():
            threshold_ref = None
        elif bool(sample.get("_central_control", False)):
            threshold_key = str(self.ews_cfg.get("threshold_key", "threshold_deaths_per_day"))
            if threshold_key not in self.threshold_meta:
                raise KeyError(
                    f"[{self.slug}] Central EWS parity requires {threshold_key!r} in "
                    f"{self.threshold_meta_path}."
                )
            threshold_ref = float(self.threshold_meta[threshold_key])
        else:
            ref_year = self.ews_threshold_ref_year if self.ews_threshold_ref_year in self.years else min(self.years)
            ref_base = base_anchor_results[ref_year]
            season_ref = np.asarray(
                ref_base.get("season_mask", _season_mask_by_md(self.dates_by_year[ref_year], self.season_start_md, self.season_end_md)),
                dtype=bool,
            )
            threshold_ref = _safe_quantile_threshold(ref_base["daily_base_total"][season_ref], sample["ews_target_days"])

        anchor_results: dict[int, dict[str, Any]] = {}
        for year in self.years:
            anchor_results[year] = self._apply_ews_to_base_year(
                year=year,
                sample=sample,
                base_res=base_anchor_results[year],
                pop_totals=base_pop_totals,
                threshold_ref=threshold_ref,
                ac_mode=ac_mode,
            )
        return anchor_results, base_pop_totals, threshold_ref

    def evaluate_branch_anchors(
        self,
        sample: dict[str, Any],
        *,
        ac_mode: str = "base",
        wh_mode: str | None = None,
        tree_enabled: bool = True,
        ews_enabled: bool = False,
        temperature_offset_c: float = 0.0,
        extreme_threshold_c: float | None = None,
        extreme_min_duration_days: int | None = None,
        base_anchor_results: dict[int, dict[str, Any]] | None = None,
        base_pop_totals: dict[int, float] | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, float], float | None]:
        if base_anchor_results is not None:
            if base_pop_totals is None:
                raise ValueError("base_pop_totals must be provided when base_anchor_results is supplied.")
            if not ews_enabled:
                return base_anchor_results, base_pop_totals, None
            return self._apply_ews_to_base_anchors(
                sample=sample,
                base_anchor_results=base_anchor_results,
                base_pop_totals=base_pop_totals,
                ac_mode=ac_mode,
            )

        anchor_results: dict[int, dict[str, Any]] = {}
        pop_totals: dict[int, float] = {}
        for year in self.years:
            res = self.evaluate_year(
                year,
                sample,
                threshold_ref=None,
                pop_totals={},
                ac_mode=ac_mode,
                wh_mode=wh_mode,
                tree_enabled=tree_enabled,
                ews_enabled=False,
                temperature_offset_c=temperature_offset_c,
                extreme_threshold_c=extreme_threshold_c,
                extreme_min_duration_days=extreme_min_duration_days,
            )
            anchor_results[year] = res
            pop_totals[year] = res["pop_total"]

        if not ews_enabled:
            return anchor_results, pop_totals, None

        return self._apply_ews_to_base_anchors(
            sample=sample,
            base_anchor_results=anchor_results,
            base_pop_totals=pop_totals,
            ac_mode=ac_mode,
        )

    def interpolate_branch_annuals(
        self,
        anchor_results: dict[int, dict[str, Any]],
        pop_totals: dict[int, float],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        anchor_years = np.array(self.years, dtype=int)
        years_all = np.arange(min(self.years), min(self.years) + HORIZON_YEARS, dtype=int)
        annual_anchor = np.array(
            [float(np.asarray(anchor_results[y]["daily_residual_total"], dtype=float).sum()) for y in self.years],
            dtype=float,
        )
        pop_anchor = np.array([float(pop_totals[y]) for y in self.years], dtype=float)
        annual_25y = _interp_1d_years(anchor_years, annual_anchor, years_all)
        pop_25y = _interp_1d_years(anchor_years, pop_anchor, years_all)
        return years_all, annual_25y, pop_25y, annual_anchor

    def _age_key(self, age: str) -> str:
        return {"<15": "lt15", "15-64": "15_64", "65+": "65p"}[age]

    def sample_dict(self, raw_row: pd.Series) -> dict[str, Any]:
        row = raw_row.to_dict()
        clim_scen = self.clim_scens[int(row["CLIM_SCEN_IDX"])]
        clim_band_requested = str(self.clim_bands[int(row["CLIM_BAND_IDX"])]).lower()
        clim_band_effective = self.effective_climate_band(clim_scen, clim_band_requested)
        if_family = self.available_if_families[int(row["IF_FAMILY_IDX"])]
        t_ref_c = TREF_BASE if if_family in ("masselot", "masselot_tail") else TREF_OPTIONS[int(row["IF_TREF_IDX"])]
        out = {
            **row,
            "year": self.years[int(row["YEAR_IDX"])],
            "baseline_mode": self.baseline_modes[int(row["BASELINE_MODE_IDX"])],
            "clim_scen": clim_scen,
            "clim_band_requested": clim_band_requested,
            "clim_band": clim_band_effective,
            "clim_source": self.clim_source_options[int(row["CLIM_SOURCE_IDX"])],
            "gcm_model": self.gcm_options[int(row["GCM_MODEL_IDX"])],
            "ac_ssp": self.ac_ssp_options[int(row["AC_SSP_IDX"])],
            "wh_enabled": self.wh_enabled_options[int(row["WH_ENABLED_IDX"])],
            "wh_case": self.wh_lut_options[int(row["WH_LUT_CASE_IDX"])],
            "cop_enabled": self.cop_enabled_options[int(row["COP_ENABLED_IDX"])],
            "cop_case": self.cop_case_options[int(row["COP_CASE_IDX"])],
            "tree_ramp_years": self.tree_ramp_options[int(row["TREE_RAMP_YEARS_IDX"])],
            "tree_start_age": self.tree_start_age_options[int(row["TREE_START_AGE_IDX"])],
            "if_family": if_family,
            "t_ref_C": t_ref_c,
            "ac_eff_scenario": self.efficacy_scenarios[int(row["AC_EFF_SCEN_IDX"])],
            "ews_interpretation": self.ews_interp_options[int(row["EWS_INTERP_IDX"])],
            "ews_cf_eff_level": self.level_options[int(row["EWS_CF_EFF_LEVEL_IDX"])],
            "ews_eff_lt15_level": self.level_options[int(row["EWS_EFF_LT15_LEVEL_IDX"])],
            "ews_eff_15_64_level": self.level_options[int(row["EWS_EFF_15_64_LEVEL_IDX"])],
            "ews_eff_65p_level": self.level_options[int(row["EWS_EFF_65P_LEVEL_IDX"])],
            "ews_overlap_level": self.level_options[int(row["EWS_OVERLAP_LEVEL_IDX"])],
            "ews_disp_lt15_level": self.level_options[int(row["EWS_DISP_LT15_LEVEL_IDX"])],
            "ews_disp_15_64_level": self.level_options[int(row["EWS_DISP_15_64_LEVEL_IDX"])],
            "ews_disp_65p_level": self.level_options[int(row["EWS_DISP_65P_LEVEL_IDX"])],
            "ews_ramp_years": self.ews_ramp_options[int(row["EWS_RAMP_YEARS_IDX"])],
            "ews_cost_model": self.ews_cost_model_options[int(row["EWS_COST_MODEL_IDX"])],
            "discount_rate": [0.02, 0.03, 0.05][int(row["DISCOUNT_RATE_IDX"])],
            "ac_capex_mult": [0.8, 1.0, 1.2][int(row["AC_CAPEX_MULT_IDX"])],
            "ac_capex_per_user": self.ac_capex_base * [0.8, 1.0, 1.2][int(row["AC_CAPEX_MULT_IDX"])],
            "ac_tariff_eur_per_kwh": self.ac_tariff_options[int(row["AC_TARIFF_EUR_PER_KWH_IDX"])],
            "ac_lifetime_years": [9, 12, 16][int(row["AC_LIFETIME_YEARS_IDX"])],
            "tree_capex_mult": [0.8, 1.0, 1.2][int(row["TREE_CAPEX_MULT_IDX"])],
            "tree_om_mult": [1.0, 5.0][int(row["TREE_OM_MULT_IDX"])],
            "elec_feedback_enabled": bool(int(row["ELEC_FEEDBACK_ENABLED_IDX"])),
            "elec_coeff_scale": float(row["ELEC_COEFF_SCALE"]),
        }
        if "EWS_TARGET_DAYS_IDX" in row:
            out["ews_target_days"] = self.ews_target_days_options[int(row["EWS_TARGET_DAYS_IDX"])]
        else:
            out["ews_target_days"] = int(self.ews_target_base)

        if "EWS_RECALIB_YEARS_IDX" in row:
            out["ews_recalib_years"] = self.ews_recalib_options[int(row["EWS_RECALIB_YEARS_IDX"])]
        else:
            out["ews_recalib_years"] = int(self.ews_recalib_base)

        if "EXTREME_THRESHOLD_PCT_IDX" in row:
            out["extreme_threshold_pct"] = float(self.extreme_threshold_options[int(row["EXTREME_THRESHOLD_PCT_IDX"])])
        else:
            out["extreme_threshold_pct"] = float(self.extreme_threshold_pct)

        if "EXTREME_MIN_DURATION_IDX" in row:
            out["extreme_min_duration_days"] = int(self.extreme_min_duration_options[int(row["EXTREME_MIN_DURATION_IDX"])])
        else:
            out["extreme_min_duration_days"] = int(self.extreme_min_duration_days)
        return out

    @staticmethod
    def _index_or_default(options: list[Any], value: Any, default: int = 0) -> int:
        """Return the exact option index, with case-insensitive string matching."""
        for idx, option in enumerate(options):
            if option == value:
                return idx
            if isinstance(option, str) and isinstance(value, str) and option.lower() == value.lower():
                return idx
        return int(np.clip(default, 0, max(len(options) - 1, 0)))

    def central_parameter_row(self) -> pd.Series:
        """Build the unsampled configuration that reproduces NB01--NB08.

        This row is evaluated and exported separately from the LHS.  It is a
        parity/control point only and is never included in PAWN or uncertainty
        quantiles.
        """
        row: dict[str, float] = {}
        for spec in self.param_specs:
            if spec.kind == "choice":
                row[spec.name] = float(len(spec.options or []) // 2)
            else:
                row[spec.name] = 0.5 * (float(spec.low) + float(spec.high))

        exp_target = str(self.cfg.get("exp_scenario", "SSP2"))
        trees_cfg = self.cfg.get("trees", {}) or {}
        wh_cfg = self.wh_cfg or {}
        cop_cfg = wh_cfg.get("cop_degradation", {}) or {}
        dyn = (self.cfg.get("vulnerability", {}) or {}).get("dynamic", {}) or {}
        thermal = dyn.get("thermal_projection", {}) or {}
        fb = dyn.get("foreign_born_projection", {}) or {}
        ue = dyn.get("unemployment_projection", {}) or {}
        k_cfg = dyn.get("k", {}) or {}
        phi_cfg = dyn.get("phi", {}) or {}

        main_family = getattr(self, "if_main_family", None)
        if main_family not in self.available_if_families:
            main_family = "masselot_tail" if "masselot_tail" in self.available_if_families else self.available_if_families[0]

        central_values = {
            "YEAR_IDX": self._index_or_default(self.years, 2050, len(self.years) - 1),
            "EXP_SSP_IDX": self._index_or_default(self.exp_ssp_options, exp_target, 0),
            "EXP_TOTAL_SCALE": 1.0,
            "BASELINE_MODE_IDX": self._index_or_default(self.baseline_modes, self.ref_mode, 0),
            "CLIM_SCEN_IDX": self._index_or_default(self.clim_scens, self.ref_scen, 0),
            "CLIM_BAND_IDX": self._index_or_default(self.clim_bands, self.ref_band, 0),
            "CLIM_SOURCE_IDX": self._index_or_default(self.clim_source_options, "bands", 0),
            "GCM_MODEL_IDX": self._index_or_default(self.gcm_options, "__none__", 0),
            "AC_SSP_IDX": self._index_or_default(self.ac_ssp_options, self.ac_ssp_base, 0),
            "WH_ENABLED_IDX": self._index_or_default(self.wh_enabled_options, self.wh_enabled_default, 0),
            "WH_LUT_CASE_IDX": self._index_or_default(
                self.wh_lut_options, wh_cfg.get("lut_case_default", "central"), 0
            ),
            "WH_RATIO": float(wh_cfg.get("dailymean_from_night_default", 0.5)),
            "COP_ENABLED_IDX": self._index_or_default(self.cop_enabled_options, self.cop_enabled_default, 0),
            "COP_CASE_IDX": self._index_or_default(self.cop_case_options, "central", 0),
            "TREE_COEFF_SCALE": 1.0,
            "TREE_CAP_UPLIFT": float(trees_cfg.get("cap_uplift_0_1", self.tree_base_cap)),
            "TREE_RAMP_YEARS_IDX": self._index_or_default(
                self.tree_ramp_options, int(trees_cfg.get("ramp_years", self.tree_ramp_base)), 0
            ),
            "TREE_START_AGE_IDX": self._index_or_default(
                self.tree_start_age_options, int(trees_cfg.get("start_age_central_years", 5)), 0
            ),
            "IF_FAMILY_IDX": self._index_or_default(self.available_if_families, main_family, 0),
            "IF_TREF_IDX": self._index_or_default(TREF_OPTIONS, TREF_BASE, 0),
            "MDD_SCALE_LT15": 1.0,
            "MDD_SCALE_15_64": 1.0,
            "MDD_SCALE_65P": 1.0,
            "DISP_FRAC": 0.0,
            "PAA_SCALE": 1.0,
            "AC_EFF_SCEN_IDX": self._index_or_default(self.efficacy_scenarios, "moderate", 0),
            "EWS_INTERP_IDX": self._index_or_default(self.ews_interp_options, self.ews_interp_base, 0),
            "EWS_CF_EFF_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_EFF_LT15_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_EFF_15_64_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_EFF_65P_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_OVERLAP_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_DISP_LT15_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_DISP_15_64_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_DISP_65P_LEVEL_IDX": self._index_or_default(self.level_options, "central", 0),
            "EWS_RAMP_YEARS_IDX": self._index_or_default(self.ews_ramp_options, self.ews_ramp_base, 0),
            "EWS_COST_MODEL_IDX": self._index_or_default(
                self.ews_cost_model_options, self.ews_cfg.get("cost_model", "pavanello"), 0
            ),
            "DISCOUNT_RATE_IDX": self._index_or_default([0.02, 0.03, 0.05], 0.03, 1),
            "AC_CAPEX_MULT_IDX": self._index_or_default([0.8, 1.0, 1.2], 1.0, 1),
            "AC_TARIFF_EUR_PER_KWH_IDX": self._index_or_default(
                self.ac_tariff_options,
                round(float(self.ac_cfg.get("tariff_eur_per_kwh", self.ac_tariff_options[0])), 2),
                0,
            ),
            "AC_LIFETIME_YEARS_IDX": self._index_or_default(
                [9, 12, 16], int(self.ac_cfg.get("lifetime_years", 12)), 1
            ),
            "TREE_CAPEX_MULT_IDX": self._index_or_default([0.8, 1.0, 1.2], 1.0, 1),
            "TREE_OM_MULT_IDX": self._index_or_default([1.0, 5.0], 1.0, 0),
            "ELEC_FEEDBACK_ENABLED_IDX": self._index_or_default([0, 1], int(self.elec_fb_enabled), 0),
            "ELEC_COEFF_SCALE": 1.0,
            "VULN_K": float(k_cfg.get("default", 0.75)),
            "VULN_PHI_2050": float(phi_cfg.get("default_2050", 0.70)),
            "VULN_DRMKC_SCALE_FB": float(fb.get("drmkc_scale", 0.04)),
            "VULN_DRMKC_SCALE_UE": float(ue.get("drmkc_scale", 0.08)),
            "VULN_GVI_SCALE_FB": float(fb.get("gvi_scale", 0.35)),
            "VULN_GVI_SCALE_UE": float(ue.get("gvi_scale", 0.50)),
            "VULN_RETROFIT_RATE": float(thermal.get("retrofit_rate_per_year", 0.0138)),
            "VULN_GROWTH_SENS": float(thermal.get("growth_sensitivity", 0.70)),
            "VULN_GROWTH_CAP": float(thermal.get("growth_cap", 0.30)),
            "VULN_NEW_BUILD": float(thermal.get("new_build_vulnerability", 0.15)),
            "EWS_TARGET_DAYS_IDX": self._index_or_default(
                self.ews_target_days_options, self.ews_target_base, 0
            ),
            "EWS_RECALIB_YEARS_IDX": self._index_or_default(
                self.ews_recalib_options, self.ews_recalib_base, 0
            ),
            "EXTREME_THRESHOLD_PCT_IDX": self._index_or_default(
                self.extreme_threshold_options, self.extreme_threshold_pct, 0
            ),
            "EXTREME_MIN_DURATION_IDX": self._index_or_default(
                self.extreme_min_duration_options, self.extreme_min_duration_days, 0
            ),
        }
        for name in row:
            if name in central_values:
                row[name] = float(central_values[name])
        central = pd.Series(row, index=[spec.name for spec in self.param_specs], dtype=float)
        central["_central_control"] = 1.0
        return central

    def compute_ac_cost_metrics(
        self,
        sample: dict[str, Any],
        years_all: np.ndarray,
        pop_25y: np.ndarray,
    ) -> dict[str, Any]:
        r = float(sample["discount_rate"])
        t_index = np.arange(1, len(years_all) + 1, dtype=float)  # end-of-year (t=1..T) to match NB08 AC cost timing
        # Maintenance recomputed from the SAMPLED CAPEX at the configured rate (config
        # canonical); the central multiplier reproduces configured maint_rate x CAPEX.
        maint_rate = self.ac_maint_rate
        maint_per_user_yr = maint_rate * float(sample["ac_capex_per_user"])
        discount_factors = (1.0 + r) ** t_index
        tariff = float(sample["ac_tariff_eur_per_kwh"])

        cost_frame = self.build_muni_ac_cost_frame(sample, years_all, pop_25y)
        if cost_frame is None:
            base_share_t = np.array(
                [self.coverage_mean_for_mode(int(y), sample["ac_ssp"], mode="base") for y in years_all],
                dtype=float,
            )
            policy_share_t = np.array(
                [self.coverage_mean_for_mode(int(y), sample["ac_ssp"], mode="policy") for y in years_all],
                dtype=float,
            )
            users_base_t = np.asarray(pop_25y, dtype=float) * base_share_t
            users_policy_t = np.asarray(pop_25y, dtype=float) * policy_share_t
            kwh_per_user_t = np.array([self.get_kwh_per_user(sample["ac_ssp"], int(y)) for y in years_all], dtype=float)

            ramp_years = int(sample["tree_ramp_years"])
            start_age = int(sample["tree_start_age"])
            maturity_t = _cohort_rollout_maturity_factor(len(years_all), ramp_years, start_age_years=start_age)
            veg_reduction_t = np.zeros(len(years_all), dtype=float)
            if sample.get("elec_feedback_enabled", False) and self.elec_fb_pw_dgvi > 0:
                pct_per_pt = self.elec_fb_pct_per_point * float(sample.get("elec_coeff_scale", 1.0))
                summer_frac = self.elec_fb_summer_months / 12.0
                veg_reduction_t = pct_per_pt * self.elec_fb_pw_dgvi * summer_frac * maturity_t
            kwh_per_user_with_trees_t = kwh_per_user_t * (1.0 - veg_reduction_t)

            kwh_base_t = np.maximum(users_base_t, 0.0) * kwh_per_user_t
            kwh_policy_t = np.maximum(users_policy_t, 0.0) * kwh_per_user_t
            kwh_base_with_trees_t = np.maximum(users_base_t, 0.0) * kwh_per_user_with_trees_t
            kwh_policy_with_trees_t = np.maximum(users_policy_t, 0.0) * kwh_per_user_with_trees_t
        else:
            ramp_years = int(sample["tree_ramp_years"])
            start_age = int(sample["tree_start_age"])
            maturity_t = _cohort_rollout_maturity_factor(len(years_all), ramp_years, start_age_years=start_age)
            maturity_map = {int(year): float(mat) for year, mat in zip(years_all, maturity_t)}

            cost_frame = cost_frame.copy()
            cost_frame["maturity_t"] = cost_frame["year"].map(maturity_map).fillna(0.0).astype(float)
            pct_per_pt = self.elec_fb_pct_per_point * float(sample.get("elec_coeff_scale", 1.0))
            summer_frac = self.elec_fb_summer_months / 12.0
            if sample.get("elec_feedback_enabled", False):
                cost_frame["veg_reduction"] = (
                    pct_per_pt
                    * cost_frame["dGVI"].clip(lower=0.0).astype(float)
                    * summer_frac
                    * cost_frame["maturity_t"]
                )
            else:
                cost_frame["veg_reduction"] = 0.0
            cost_frame["kwh_per_user_with_trees"] = cost_frame["kwh_per_user_t"] * (1.0 - cost_frame["veg_reduction"])

            users_base_t = (
                cost_frame.assign(users=lambda d: d["pop_muni"] * d["base_share_t"])
                .groupby("year")["users"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )
            users_policy_t = (
                cost_frame.assign(users=lambda d: d["pop_muni"] * d["policy_share_t"])
                .groupby("year")["users"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )
            kwh_base_t = (
                cost_frame.assign(kwh=lambda d: d["pop_muni"] * d["base_share_t"] * d["kwh_per_user_t"])
                .groupby("year")["kwh"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )
            kwh_policy_t = (
                cost_frame.assign(kwh=lambda d: d["pop_muni"] * d["policy_share_t"] * d["kwh_per_user_t"])
                .groupby("year")["kwh"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )
            kwh_base_with_trees_t = (
                cost_frame.assign(kwh=lambda d: d["pop_muni"] * d["base_share_t"] * d["kwh_per_user_with_trees"])
                .groupby("year")["kwh"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )
            kwh_policy_with_trees_t = (
                cost_frame.assign(kwh=lambda d: d["pop_muni"] * d["policy_share_t"] * d["kwh_per_user_with_trees"])
                .groupby("year")["kwh"]
                .sum()
                .reindex(years_all, fill_value=0.0)
                .to_numpy(float)
            )

        added_users_t = np.maximum(users_policy_t - users_base_t, 0.0)
        new_users_t = np.empty_like(added_users_t)
        new_users_t[0] = added_users_t[0]
        new_users_t[1:] = np.maximum(added_users_t[1:] - added_users_t[:-1], 0.0)

        capex_stream_t = _capex_replacement_stream(
            new_users_t,
            float(sample["ac_capex_per_user"]),
            int(sample["ac_lifetime_years"]),
        )
        maint_stream_t = np.maximum(added_users_t, 0.0) * maint_per_user_yr
        pv_capex_stream_t = capex_stream_t / discount_factors
        pv_maint_stream_t = maint_stream_t / discount_factors
        pv_capex = float(pv_capex_stream_t.sum())
        pv_maint = float(pv_maint_stream_t.sum())

        kwh_inc_standalone_t = kwh_policy_t - kwh_base_t
        kwh_inc_with_trees_t = kwh_policy_with_trees_t - kwh_base_with_trees_t

        elec_stream_t = np.maximum(kwh_inc_standalone_t, 0.0) * tariff
        elec_with_trees_stream_t = np.maximum(kwh_inc_with_trees_t, 0.0) * tariff
        pv_elec_stream_t = elec_stream_t / discount_factors
        pv_elec_with_trees_stream_t = elec_with_trees_stream_t / discount_factors
        pv_elec_standalone = float(pv_elec_stream_t.sum())
        pv_elec_with_trees = float(pv_elec_with_trees_stream_t.sum())
        pv_total_standalone = pv_capex + pv_maint + pv_elec_standalone
        pv_total_with_trees = pv_capex + pv_maint + pv_elec_with_trees

        kwh_saved_base_users_t = kwh_base_t - kwh_base_with_trees_t
        kwh_saved_all_t = kwh_policy_t - kwh_policy_with_trees_t
        pv_veg_eur_base = float(np.sum(kwh_saved_base_users_t * tariff / discount_factors))
        pv_veg_eur_all = float(np.sum(kwh_saved_all_t * tariff / discount_factors))
        cum_veg_kwh_base = float(kwh_saved_base_users_t.sum())
        cum_veg_kwh_all = float(kwh_saved_all_t.sum())
        cum_veg_co2_base_t = cum_veg_kwh_base * self.elec_fb_co2_per_kwh / 1e6
        cum_veg_co2_all_t = cum_veg_kwh_all * self.elec_fb_co2_per_kwh / 1e6

        return {
            "ac_pv_capex_25y": pv_capex,
            "ac_pv_maint_25y": pv_maint,
            "ac_pv_elec_25y": pv_elec_standalone,
            "ac_pv_elec_with_trees_25y": pv_elec_with_trees,
            "ac_pv_elec_no_veg_25y": pv_elec_standalone,
            "ac_pv_elec_veg_saving": pv_elec_standalone - pv_elec_with_trees,
            "ac_pv_cost_25y": pv_total_standalone,
            "ac_pv_cost_with_trees_25y": pv_total_with_trees,
            "ac_added_users_final": float(added_users_t[-1]) if added_users_t.size else 0.0,
            "elec_pv_savings": pv_veg_eur_all,
            "elec_kwh_25y": cum_veg_kwh_all,
            "elec_co2_t_25y": cum_veg_co2_all_t,
            "tree_elec_pv_savings_base_users": pv_veg_eur_base,
            "tree_elec_pv_savings_all_users": pv_veg_eur_all,
            "tree_elec_kwh_base_users_25y": cum_veg_kwh_base,
            "tree_elec_kwh_all_users_25y": cum_veg_kwh_all,
            "tree_elec_co2_base_users_t_25y": cum_veg_co2_base_t,
            "tree_elec_co2_all_users_t_25y": cum_veg_co2_all_t,
            "_ac_cost_streams": {
                "capex": capex_stream_t,
                "maintenance": maint_stream_t,
                "electricity": elec_stream_t,
                "electricity_with_trees": elec_with_trees_stream_t,
                "total": capex_stream_t + maint_stream_t + elec_stream_t,
                "total_with_trees": capex_stream_t + maint_stream_t + elec_with_trees_stream_t,
                "pv_capex": pv_capex_stream_t,
                "pv_maintenance": pv_maint_stream_t,
                "pv_electricity": pv_elec_stream_t,
                "pv_electricity_with_trees": pv_elec_with_trees_stream_t,
                "pv_total": pv_capex_stream_t + pv_maint_stream_t + pv_elec_stream_t,
                "pv_total_with_trees": pv_capex_stream_t + pv_maint_stream_t + pv_elec_with_trees_stream_t,
            },
        }

    def compute_tree_cost_metrics(self, sample: dict[str, Any]) -> dict[str, Any]:
        trees_cfg = self.cfg.get("trees", {})
        years = HORIZON_YEARS
        r = float(sample["discount_rate"])

        # Active YAML config is canonical, exactly as in NB08.  The NB07 JSON
        # supplies derived policy quantities and is only a fallback/validation
        # source for calibrated unit costs.
        base_capex_per_tree = float(
            trees_cfg.get("capex_per_tree_eur", self.tree_cost_params.get("capex_per_tree", 0.0))
        )
        base_capex_per_index = float(
            trees_cfg.get("capex_per_index_pt_eur", self.tree_cost_params.get("capex_per_index_pt", 0.0))
        )
        # UQ scales the CALIBRATED per-GVI-point CAPEX by a dimensionless multiplier (0.8/1.0/1.2).
        capex_scale = float(sample["tree_capex_mult"])
        capex_per_index = base_capex_per_index * capex_scale

        base_om_per_tree = float(
            trees_cfg.get("om_per_tree_per_year_eur", self.tree_cost_params.get("om_per_tree_yr", 0.0))
        )
        # NB08 derives per-GVI-point O&M from the configured per-tree
        # O&M/CAPEX ratio; it does not take an independently cached JSON value.
        base_om_per_index = float(
            (base_om_per_tree / base_capex_per_tree) * base_capex_per_index
            if (base_capex_per_tree > 0 and base_capex_per_index > 0)
            else 0.0
        )
        # UQ scales the CALIBRATED per-GVI-point O&M by a dimensionless multiplier (1.0/5.0).
        om_scale = float(sample["tree_om_mult"])
        om_per_index = base_om_per_index * om_scale

        delta_index_total = float(
            self.tree_cost_params.get(
                "delta_index_total",
                self.tree_cost_params.get("delta_gvi_total", self.tree_cost_params.get("total_dGVI_points", 0.0)),
            )
        )
        lifetime = int(trees_cfg.get("lifetime_years", self.tree_cost_params.get("lifetime_years", HORIZON_YEARS)))
        capex_stream = _tree_capex_linear_stream(delta_index_total, years, capex_per_index)
        discount = (1.0 + r) ** np.arange(1, years + 1, dtype=float)
        pv_capex_stream = capex_stream / discount
        pv_capex = float(pv_capex_stream.sum())
        pv_om, om_stream = _npv_om_cohorts_scaled(
            delta_index_total,
            years,
            r,
            om_per_index,
            int(sample["tree_ramp_years"]),
            lifetime,
            int(sample["tree_start_age"]),
        )
        return {
            "tree_pv_capex_25y": pv_capex,
            "tree_pv_om_25y": pv_om,
            "tree_pv_cost_25y": pv_capex + pv_om,
            "tree_delta_index_total": delta_index_total,
            "_tree_cost_streams": {
                "capex": capex_stream,
                "om": om_stream,
                "total": capex_stream + om_stream,
                "pv_capex": pv_capex_stream,
                "pv_om": om_stream / discount,
                "pv_total": pv_capex_stream + om_stream / discount,
            },
        }

    def build_policy_trajectory_rows(
        self,
        sample_idx: int,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return the complete 25-year policy accounting in long format."""
        years = np.asarray(result["_years_all"], dtype=int)
        branches = result["_policy_branch_annuals"]
        effects = result["_policy_branch_effects"]
        ac_cost = result["_ac_cost_streams"]
        tree_cost = result["_tree_cost_streams"]
        ews_cost = result["_ews_cost_streams"]
        zeros = np.zeros(years.size, dtype=float)

        annual_costs = {
            "reference": zeros,
            "ac_policy_gross": np.asarray(ac_cost["total"], dtype=float),
            "ac_policy_net": np.asarray(ac_cost["total"], dtype=float),
            "ac_policy_net_with_tree_feedback": np.asarray(ac_cost["total_with_trees"], dtype=float),
            "tree_policy": np.asarray(tree_cost["total"], dtype=float),
            "ews_policy": np.asarray(ews_cost["total"], dtype=float),
            "ac_tree_policy_gross": np.asarray(ac_cost["total_with_trees"], dtype=float)
            + np.asarray(tree_cost["total"], dtype=float),
            "ac_tree_policy_net": np.asarray(ac_cost["total_with_trees"], dtype=float)
            + np.asarray(tree_cost["total"], dtype=float),
        }
        pv_costs = {
            "reference": zeros,
            "ac_policy_gross": np.asarray(ac_cost["pv_total"], dtype=float),
            "ac_policy_net": np.asarray(ac_cost["pv_total"], dtype=float),
            "ac_policy_net_with_tree_feedback": np.asarray(ac_cost["pv_total_with_trees"], dtype=float),
            "tree_policy": np.asarray(tree_cost["pv_total"], dtype=float),
            "ews_policy": np.asarray(ews_cost["pv_total"], dtype=float),
            "ac_tree_policy_gross": np.asarray(ac_cost["pv_total_with_trees"], dtype=float)
            + np.asarray(tree_cost["pv_total"], dtype=float),
            "ac_tree_policy_net": np.asarray(ac_cost["pv_total_with_trees"], dtype=float)
            + np.asarray(tree_cost["pv_total"], dtype=float),
        }
        waste_heat = {
            "reference": zeros,
            "ac_policy_gross": zeros,
            "ac_policy_net": np.asarray(effects["ac_penalty_raw_25y"], dtype=float),
            "ac_policy_net_with_tree_feedback": np.asarray(effects["ac_penalty_with_trees_25y"], dtype=float),
            "tree_policy": zeros,
            "ews_policy": zeros,
            "ac_tree_policy_gross": zeros,
            "ac_tree_policy_net": np.asarray(effects["ac_penalty_with_trees_25y"], dtype=float),
        }
        branch_types = {
            "reference": "reference",
            "ac_policy_gross": "standalone",
            "ac_policy_net": "standalone",
            "ac_policy_net_with_tree_feedback": "interaction",
            "tree_policy": "standalone",
            "ews_policy": "standalone",
            "ac_tree_policy_gross": "combined",
            "ac_tree_policy_net": "combined",
        }
        reference = np.asarray(branches["reference"], dtype=float)
        maturity = np.asarray(effects["tree_maturity_25y"], dtype=float)
        lambda_y = np.asarray(effects["lambda_y_25y"], dtype=float)
        rows: list[dict[str, Any]] = []
        for branch in BRANCH_NAMES:
            deaths = np.asarray(branches[branch], dtype=float)
            avoided = reference - deaths
            for idx, year in enumerate(years):
                rows.append(
                    {
                        "sample_idx": int(sample_idx),
                        "year": int(year),
                        "branch": branch,
                        "branch_type": branch_types[branch],
                        "annual_deaths": float(deaths[idx]),
                        "avoided_deaths_vs_reference": float(avoided[idx]),
                        "waste_heat_penalty_deaths": float(waste_heat[branch][idx]),
                        "annual_cost_eur": float(annual_costs[branch][idx]),
                        "pv_cost_eur": float(pv_costs[branch][idx]),
                        "tree_maturity_factor": float(maturity[idx]),
                        "ac_activity_multiplier_with_trees": float(lambda_y[idx]),
                    }
                )
        return rows

    def validate_sample_output(
        self,
        sample_idx: int,
        sample: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Fail-fast mathematical and provenance checks for one LHS draw."""
        years = np.asarray(result["_years_all"], dtype=int)
        expected_len = HORIZON_YEARS
        rows: list[dict[str, Any]] = []
        failures: list[str] = []

        def add_status(metric: str, status: str, max_abs_error: float = 0.0) -> None:
            rows.append(
                {
                    "sample_idx": int(sample_idx),
                    "metric": metric,
                    "max_abs_error": float(max_abs_error),
                    "status": status,
                }
            )
            if status != "pass":
                failures.append(metric)

        def check_array(metric: str, value: Any) -> np.ndarray:
            arr = np.asarray(value, dtype=float)
            length_ok = arr.ndim == 1 and arr.size == expected_len
            finite_ok = length_ok and bool(np.all(np.isfinite(arr)))
            add_status(f"array::{metric}", "pass" if finite_ok else "failed")
            return arr

        def check_identity(metric: str, left: Any, right: Any) -> None:
            lhs = np.asarray(left, dtype=float)
            rhs = np.asarray(right, dtype=float)
            if lhs.shape != rhs.shape or lhs.size == 0:
                add_status(metric, "failed", np.inf)
                return
            error = np.abs(lhs - rhs)
            tolerance = 1e-7 + 1e-10 * np.maximum(np.abs(lhs), np.abs(rhs))
            passed = bool(np.all(np.isfinite(lhs)) and np.all(np.isfinite(rhs)) and np.all(error <= tolerance))
            add_status(metric, "pass" if passed else "failed", float(np.max(error)))

        def check_nonnegative(metric: str, value: Any) -> None:
            arr = np.asarray(value, dtype=float)
            finite = bool(arr.size and np.all(np.isfinite(arr)))
            minimum = float(np.min(arr)) if finite else -np.inf
            add_status(metric, "pass" if finite and minimum >= -1e-7 else "failed", max(-minimum, 0.0))

        add_status("years::25_consecutive", "pass" if (
            years.size == expected_len and np.array_equal(np.diff(years), np.ones(expected_len - 1, dtype=int))
        ) else "failed")

        branches = {
            name: check_array(f"branch::{name}", result["_policy_branch_annuals"][name])
            for name in BRANCH_NAMES
        }
        effects = {
            name: check_array(f"effect::{name}", value)
            for name, value in result["_policy_branch_effects"].items()
        }
        for family in ("_ac_cost_streams", "_tree_cost_streams", "_ews_cost_streams"):
            for name, value in result[family].items():
                check_array(f"cost::{family[1:]}::{name}", value)

        reference = branches["reference"]
        check_identity("identity::ac_gross_avoided", effects["ac_gross_avoided_25y"], reference - branches["ac_policy_gross"])
        check_identity("identity::ac_net_avoided", effects["ac_net_avoided_25y"], reference - branches["ac_policy_net"])
        check_identity(
            "identity::ac_net_equals_gross_minus_waste_heat",
            effects["ac_net_avoided_25y"],
            effects["ac_gross_avoided_25y"] - effects["ac_penalty_raw_25y"],
        )
        check_identity("identity::tree_avoided", effects["tree_avoided_25y"], reference - branches["tree_policy"])
        check_identity(
            "identity::ac_net_with_tree_feedback",
            effects["ac_net_with_tree_feedback_25y"],
            reference - branches["ac_policy_net_with_tree_feedback"],
        )
        check_identity(
            "identity::ac_tree_feedback_net_equals_ac_gross_minus_waste_heat",
            effects["ac_net_with_tree_feedback_25y"],
            effects["ac_gross_avoided_25y"] - effects["ac_penalty_with_trees_25y"],
        )
        check_identity(
            "identity::ews_standalone_provenance",
            effects["ews_reference_avoided_25y"],
            reference - branches["ews_policy"],
        )
        check_identity(
            "identity::ac_tree_gross_avoided",
            effects["ac_tree_gross_avoided_25y"],
            reference - branches["ac_tree_policy_gross"],
        )
        check_identity(
            "identity::ac_tree_net_avoided",
            effects["ac_net_with_trees_25y"],
            reference - branches["ac_tree_policy_net"],
        )
        check_identity(
            "identity::ac_tree_net_equals_gross_minus_waste_heat",
            effects["ac_net_with_trees_25y"],
            effects["ac_tree_gross_avoided_25y"] - effects["ac_penalty_with_trees_25y"],
        )
        check_identity(
            "identity::trees_on_top_of_ac",
            effects["trees_on_top_25y"],
            branches["ac_policy_gross"] - branches["ac_tree_policy_gross"],
        )

        scalar_identities = {
            "reference_deaths_25y_cum": reference.sum(),
            "ac_gross_branch_deaths_25y_cum": branches["ac_policy_gross"].sum(),
            "ac_net_branch_deaths_25y_cum": branches["ac_policy_net"].sum(),
            "ac_gross_avoided_deaths_25y_cum": effects["ac_gross_avoided_25y"].sum(),
            "ac_net_avoided_deaths_25y_cum": effects["ac_net_avoided_25y"].sum(),
            "ac_waste_heat_penalty_25y_cum": effects["ac_penalty_raw_25y"].sum(),
            "tree_avoided_deaths_25y_cum": effects["tree_avoided_25y"].sum(),
            "tree_on_top_of_ac_avoided_deaths_25y_cum": effects["trees_on_top_25y"].sum(),
            "tree_branch_deaths_25y_cum": branches["tree_policy"].sum(),
            "ews_net_avoided_deaths_25y_cum": effects["ews_reference_avoided_25y"].sum(),
            "ews_branch_deaths_25y_cum": branches["ews_policy"].sum(),
            "ac_with_trees_gross_avoided_deaths_25y_cum": effects["ac_gross_avoided_25y"].sum(),
            "ac_with_trees_net_avoided_deaths_25y_cum": effects["ac_net_with_tree_feedback_25y"].sum(),
            "ac_with_trees_gross_branch_deaths_25y_cum": branches["ac_policy_gross"].sum(),
            "ac_with_trees_net_branch_deaths_25y_cum": branches["ac_policy_net_with_tree_feedback"].sum(),
            "ac_with_trees_waste_heat_penalty_25y_cum": effects["ac_penalty_with_trees_25y"].sum(),
            "combined_ac_tree_gross_avoided_deaths_25y_cum": effects["ac_tree_gross_avoided_25y"].sum(),
            "combined_ac_tree_net_avoided_deaths_25y_cum": effects["ac_net_with_trees_25y"].sum(),
            "combined_ac_tree_gross_branch_deaths_25y_cum": branches["ac_tree_policy_gross"].sum(),
            "combined_ac_tree_net_branch_deaths_25y_cum": branches["ac_tree_policy_net"].sum(),
        }
        for name, expected in scalar_identities.items():
            check_identity(f"identity::scalar::{name}", result[name], expected)

        ac_cost = result["_ac_cost_streams"]
        tree_cost = result["_tree_cost_streams"]
        ews_cost = result["_ews_cost_streams"]
        for family_name, streams in (
            ("ac", ac_cost),
            ("tree", tree_cost),
            ("ews", ews_cost),
        ):
            for stream_name, stream in streams.items():
                check_nonnegative(f"ordering::{family_name}_cost::{stream_name}", stream)
        check_identity("identity::ac_annual_total", ac_cost["total"], ac_cost["capex"] + ac_cost["maintenance"] + ac_cost["electricity"])
        check_identity("identity::ac_tree_annual_total", ac_cost["total_with_trees"], ac_cost["capex"] + ac_cost["maintenance"] + ac_cost["electricity_with_trees"])
        check_identity("identity::tree_annual_total", tree_cost["total"], tree_cost["capex"] + tree_cost["om"])
        check_identity("identity::ews_annual_total", ews_cost["total"], ews_cost["capex"] + ews_cost["opex_fixed"] + ews_cost["opex_variable"])
        check_identity(
            "identity::ac_pv_total",
            ac_cost["pv_total"],
            ac_cost["pv_capex"] + ac_cost["pv_maintenance"] + ac_cost["pv_electricity"],
        )
        check_identity(
            "identity::ac_tree_pv_total",
            ac_cost["pv_total_with_trees"],
            ac_cost["pv_capex"] + ac_cost["pv_maintenance"] + ac_cost["pv_electricity_with_trees"],
        )
        check_identity(
            "identity::tree_pv_total",
            tree_cost["pv_total"],
            tree_cost["pv_capex"] + tree_cost["pv_om"],
        )
        discount_rate = float(sample["discount_rate"])
        end_of_year_discount = (1.0 + discount_rate) ** np.arange(1, expected_len + 1, dtype=float)
        start_of_year_discount = (1.0 + discount_rate) ** np.arange(0, expected_len, dtype=float)
        check_identity("timing::ac_pv", ac_cost["pv_total"], ac_cost["total"] / end_of_year_discount)
        check_identity("timing::tree_pv", tree_cost["pv_total"], tree_cost["total"] / end_of_year_discount)
        check_identity("timing::ews_pv", ews_cost["pv_total"], ews_cost["total"] / start_of_year_discount)
        cost_scalars = {
            "ac_pv_capex_25y": np.sum(ac_cost["pv_capex"]),
            "ac_pv_maint_25y": np.sum(ac_cost["pv_maintenance"]),
            "ac_pv_elec_25y": np.sum(ac_cost["pv_electricity"]),
            "ac_pv_cost_25y": np.sum(ac_cost["pv_total"]),
            "ac_pv_elec_with_trees_25y": np.sum(ac_cost["pv_electricity_with_trees"]),
            "ac_pv_cost_with_trees_25y": np.sum(ac_cost["pv_total_with_trees"]),
            "tree_pv_capex_25y": np.sum(tree_cost["pv_capex"]),
            "tree_pv_om_25y": np.sum(tree_cost["pv_om"]),
            "tree_pv_cost_25y": np.sum(tree_cost["pv_total"]),
            "ews_pv_cost_25y": np.sum(ews_cost["pv_total"]),
            "combined_ac_tree_pv_cost_25y": np.sum(ac_cost["pv_total_with_trees"])
            + np.sum(tree_cost["pv_total"]),
        }
        for name, expected in cost_scalars.items():
            check_identity(f"identity::cost_scalar::{name}", result[name], expected)

        coverage_violation = 0.0
        coverage_finite = True
        for year in years:
            base = np.asarray(self.coverage_for_sample(int(year), int(sample["ac_ssp"]), mode="base"), dtype=float)
            policy = np.asarray(self.coverage_for_sample(int(year), int(sample["ac_ssp"]), mode="policy"), dtype=float)
            valid = np.isfinite(base) & np.isfinite(policy)
            if not np.any(valid):
                coverage_finite = False
                break
            coverage_violation = max(coverage_violation, float(np.max(base[valid] - policy[valid])))
        add_status(
            "ordering::ac_policy_coverage_ge_reference",
            "pass" if coverage_finite and coverage_violation <= 1e-7 else "failed",
            max(coverage_violation, 0.0),
        )

        if failures:
            raise RuntimeError(
                f"[{self.slug}] sample {sample_idx} mathematical QA failed: {', '.join(failures)}"
            )
        return rows

    def validate_uq_sample_outputs(
        self,
        samples_df: pd.DataFrame,
        trajectories_df: pd.DataFrame | None = None,
        sample_qa_df: pd.DataFrame | None = None,
    ) -> None:
        critical_cols = [
            "aai_agg",
            "annual_deaths",
            "ews_net_avoided_deaths_25y_cum",
            "tree_avoided_deaths_25y_cum",
            "ac_pv_cost_25y",
            "ac_added_users_final",
            "ac_gross_avoided_deaths_25y_cum",
            "ac_net_avoided_deaths_25y_cum",
            "ac_waste_heat_penalty_25y_cum",
        ]
        qa_rows: list[dict[str, Any]] = []
        for col in critical_cols:
            if col not in samples_df.columns:
                qa_rows.append(
                    {
                        "metric": col,
                        "n_total": int(len(samples_df)),
                        "n_finite": 0,
                        "min": np.nan,
                        "median": np.nan,
                        "max": np.nan,
                        "status": "missing_column",
                    }
                )
                continue
            vals = pd.to_numeric(samples_df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
            finite = vals[np.isfinite(vals)]
            qa_rows.append(
                {
                    "metric": col,
                    "n_total": int(vals.size),
                    "n_finite": int(finite.size),
                    "min": float(finite.min()) if not finite.empty else np.nan,
                    "median": float(finite.median()) if not finite.empty else np.nan,
                    "max": float(finite.max()) if not finite.empty else np.nan,
                    "status": (
                        "ok"
                        if int(finite.size) == int(vals.size)
                        else "no_finite_samples"
                        if finite.empty
                        else "nonfinite_samples"
                    ),
                }
            )

        qa_df = pd.DataFrame(qa_rows)

        sample_index_ok = False
        if "sample_idx" in samples_df.columns:
            sample_indices = pd.to_numeric(samples_df["sample_idx"], errors="coerce")
            sample_index_ok = bool(
                sample_indices.notna().all()
                and sample_indices.astype(int).is_unique
                and np.array_equal(
                    np.sort(sample_indices.astype(int).to_numpy()),
                    np.arange(len(samples_df), dtype=int),
                )
            )
        qa_df = pd.concat(
            [
                qa_df,
                pd.DataFrame(
                    [
                        {
                            "metric": "sample_index_contract",
                            "n_total": int(len(samples_df)),
                            "n_finite": int(len(samples_df)) if sample_index_ok else 0,
                            "status": "ok" if sample_index_ok else "record_contract_failed",
                        }
                    ]
                ),
            ],
            ignore_index=True,
            sort=False,
        )

        trajectory_status = "ok"
        trajectory_error = 0.0
        expected_trajectory_rows = int(len(samples_df) * HORIZON_YEARS * len(BRANCH_NAMES))
        required_trajectory_columns = {
            "sample_idx",
            "year",
            "branch",
            "annual_deaths",
            "avoided_deaths_vs_reference",
            "annual_cost_eur",
            "pv_cost_eur",
        }
        if trajectories_df is None or not required_trajectory_columns.issubset(trajectories_df.columns):
            trajectory_status = "record_contract_failed"
        elif len(trajectories_df) != expected_trajectory_rows:
            trajectory_status = "record_contract_failed"
        else:
            trajectory_keys = trajectories_df[["sample_idx", "year", "branch"]].copy()
            if trajectory_keys.duplicated().any():
                trajectory_status = "record_contract_failed"
            expected_years = set(range(int(min(self.years)), int(min(self.years)) + HORIZON_YEARS))
            expected_samples = set(range(len(samples_df)))
            found_samples = set(pd.to_numeric(trajectory_keys["sample_idx"], errors="coerce").dropna().astype(int))
            if found_samples != expected_samples:
                trajectory_status = "record_contract_failed"
            if trajectory_status == "ok":
                for _, group in trajectories_df.groupby("sample_idx", sort=False):
                    found_years = set(pd.to_numeric(group["year"], errors="coerce").dropna().astype(int))
                    found_branches = set(group["branch"].astype(str))
                    if found_years != expected_years or found_branches != set(BRANCH_NAMES):
                        trajectory_status = "record_contract_failed"
                        break
            numeric_columns = [
                "annual_deaths",
                "avoided_deaths_vs_reference",
                "annual_cost_eur",
                "pv_cost_eur",
            ]
            if trajectory_status == "ok":
                numeric = trajectories_df[numeric_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
                if not np.all(np.isfinite(numeric)):
                    trajectory_status = "nonfinite_samples"
            if trajectory_status == "ok":
                reference = (
                    trajectories_df.loc[trajectories_df["branch"] == "reference", ["sample_idx", "year", "annual_deaths"]]
                    .rename(columns={"annual_deaths": "reference_deaths"})
                )
                merged = trajectories_df.merge(reference, on=["sample_idx", "year"], how="left", validate="many_to_one")
                lhs = pd.to_numeric(merged["avoided_deaths_vs_reference"], errors="coerce").to_numpy(float)
                rhs = (
                    pd.to_numeric(merged["reference_deaths"], errors="coerce").to_numpy(float)
                    - pd.to_numeric(merged["annual_deaths"], errors="coerce").to_numpy(float)
                )
                errors = np.abs(lhs - rhs)
                tolerance = 1e-7 + 1e-10 * np.maximum(np.abs(lhs), np.abs(rhs))
                trajectory_error = float(np.max(errors)) if errors.size else np.inf
                if not np.all(np.isfinite(lhs) & np.isfinite(rhs) & (errors <= tolerance)):
                    trajectory_status = "identity_failed"
        qa_df = pd.concat(
            [
                qa_df,
                pd.DataFrame(
                    [
                        {
                            "metric": "trajectory_export_contract",
                            "n_total": int(len(trajectories_df)) if trajectories_df is not None else 0,
                            "n_finite": expected_trajectory_rows if trajectory_status == "ok" else 0,
                            "max": trajectory_error,
                            "status": trajectory_status,
                        }
                    ]
                ),
            ],
            ignore_index=True,
            sort=False,
        )
        expected_ac_delta = any(
            self.interpolate_coverage_mean(int(y), "policy") - self.interpolate_coverage_mean(int(y), "base") > 1e-6
            for y in self.coverage_years
        )
        if expected_ac_delta and "ac_added_users_final" in samples_df.columns:
            added = pd.to_numeric(samples_df["ac_added_users_final"], errors="coerce").replace([np.inf, -np.inf], np.nan)
            finite_added = added[np.isfinite(added)]
            if finite_added.empty or float(finite_added.max()) <= 0.0:
                qa_df.loc[qa_df["metric"] == "ac_added_users_final", "status"] = "unexpected_all_zero"

        # Aggregate identities repeat the per-sample checks on the assembled
        # data frame.  Specify them as named linear combinations so a missing
        # column is reported cleanly instead of triggering a Python TypeError
        # while constructing the right-hand side.
        identities = {
            "ac_net_equals_gross_minus_waste_heat": (
                "ac_net_avoided_deaths_25y_cum",
                ((1.0, "ac_gross_avoided_deaths_25y_cum"), (-1.0, "ac_waste_heat_penalty_25y_cum")),
            ),
            "ac_total_cost_equals_components": (
                "ac_pv_cost_25y",
                ((1.0, "ac_pv_capex_25y"), (1.0, "ac_pv_maint_25y"), (1.0, "ac_pv_elec_25y")),
            ),
            "tree_total_cost_equals_components": (
                "tree_pv_cost_25y",
                ((1.0, "tree_pv_capex_25y"), (1.0, "tree_pv_om_25y")),
            ),
            "ews_branch_equals_reference_minus_avoided": (
                "ews_branch_deaths_25y_cum",
                ((1.0, "reference_deaths_25y_cum"), (-1.0, "ews_net_avoided_deaths_25y_cum")),
            ),
            "tree_branch_equals_reference_minus_avoided": (
                "tree_branch_deaths_25y_cum",
                ((1.0, "reference_deaths_25y_cum"), (-1.0, "tree_avoided_deaths_25y_cum")),
            ),
            "ac_tree_feedback_net_equals_gross_minus_waste_heat": (
                "ac_with_trees_net_avoided_deaths_25y_cum",
                (
                    (1.0, "ac_with_trees_gross_avoided_deaths_25y_cum"),
                    (-1.0, "ac_with_trees_waste_heat_penalty_25y_cum"),
                ),
            ),
            "combined_ac_tree_net_equals_gross_minus_waste_heat": (
                "combined_ac_tree_net_avoided_deaths_25y_cum",
                (
                    (1.0, "combined_ac_tree_gross_avoided_deaths_25y_cum"),
                    (-1.0, "ac_with_trees_waste_heat_penalty_25y_cum"),
                ),
            ),
            "combined_ac_tree_cost_equals_ac_interaction_plus_tree": (
                "combined_ac_tree_pv_cost_25y",
                ((1.0, "ac_pv_cost_with_trees_25y"), (1.0, "tree_pv_cost_25y")),
            ),
        }
        identity_rows: list[dict[str, Any]] = []
        for metric, (left_name, right_terms) in identities.items():
            required = [left_name, *(name for _, name in right_terms)]
            if any(name not in samples_df.columns for name in required):
                identity_rows.append({"metric": metric, "status": "missing_column"})
                continue
            lhs = pd.to_numeric(samples_df[left_name], errors="coerce").to_numpy(float)
            rhs = np.zeros(len(samples_df), dtype=float)
            for coefficient, name in right_terms:
                rhs += float(coefficient) * pd.to_numeric(samples_df[name], errors="coerce").to_numpy(float)
            err = np.abs(lhs - rhs)
            tol = 1e-7 + 1e-10 * np.maximum(np.abs(lhs), np.abs(rhs))
            ok = np.isfinite(lhs) & np.isfinite(rhs) & (err <= tol)
            identity_rows.append(
                {
                    "metric": metric,
                    "n_total": int(lhs.size),
                    "n_finite": int(np.sum(np.isfinite(lhs) & np.isfinite(rhs))),
                    "min": np.nan,
                    "median": float(np.nanmedian(err)) if err.size else np.nan,
                    "max": float(np.nanmax(err)) if err.size else np.nan,
                    "status": "ok" if bool(np.all(ok)) else "identity_failed",
                }
            )
        qa_df = pd.concat([qa_df, pd.DataFrame(identity_rows)], ignore_index=True, sort=False)

        if sample_qa_df is not None:
            if sample_qa_df.empty:
                qa_df = pd.concat(
                    [qa_df, pd.DataFrame([{"metric": "per_sample_mathematical_qa", "status": "no_rows"}])],
                    ignore_index=True,
                    sort=False,
                )
            else:
                failed = sample_qa_df.loc[sample_qa_df["status"] != "pass"]
                qa_df = pd.concat(
                    [
                        qa_df,
                        pd.DataFrame(
                            [
                                {
                                    "metric": "per_sample_mathematical_qa",
                                    "n_total": int(len(sample_qa_df)),
                                    "n_finite": int(len(sample_qa_df) - len(failed)),
                                    "min": np.nan,
                                    "median": np.nan,
                                    "max": float(pd.to_numeric(sample_qa_df["max_abs_error"], errors="coerce").max()),
                                    "status": "ok" if failed.empty else "identity_failed",
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                    sort=False,
                )

        qa_path = self.unc_dir / f"uq_output_qa_{self.slug}_improved_fast.csv"
        _atomic_write_csv(qa_path, qa_df, index=False)

        bad = qa_df[
            qa_df["status"].isin(
                [
                    "missing_column",
                    "no_finite_samples",
                    "nonfinite_samples",
                    "unexpected_all_zero",
                    "identity_failed",
                    "no_rows",
                    "record_contract_failed",
                ]
            )
        ]
        if not bad.empty:
            details = ", ".join(f"{row.metric}={row.status}" for row in bad.itertuples())
            raise RuntimeError(f"[{self.slug}] Critical NB09 UQ output QA failed: {details}. See {qa_path}")

    def validate_central_against_deterministic(self, result: dict[str, Any]) -> Path:
        """Validate the unsampled central point against NB05--NB08 artifacts.

        The core deterministic tables from NB06--NB08 are mandatory. Any
        missing target, numerical disagreement, or artifact generated from
        cost inputs that no longer match the active city config aborts the
        production run before the LHS starts. Optional granular NB05/NB07
        interim artifacts add checks when present but are not substitutes for
        the mandatory published tables.
        """
        rows: list[dict[str, Any]] = []

        def record(metric: str, observed: Any, expected: Any, source: Path) -> None:
            obs = float(observed)
            exp = float(expected)
            abs_error = abs(obs - exp)
            rel_error = abs_error / max(abs(exp), 1e-12)
            passed = bool(np.isfinite(obs) and np.isfinite(exp) and np.isclose(obs, exp, rtol=1e-4, atol=1e-5))
            rows.append(
                {
                    "metric": metric,
                    "central_nb09": obs,
                    "deterministic_nb01_08": exp,
                    "absolute_error": abs_error,
                    "relative_error": rel_error,
                    "status": "pass" if passed else "mismatch",
                    "source": str(source),
                }
            )

        def record_input(metric: str, active: Any, artifact: Any, source: Path) -> None:
            numeric = isinstance(active, (int, float, np.integer, np.floating)) and isinstance(
                artifact, (int, float, np.integer, np.floating)
            )
            if numeric:
                active_num = float(active)
                artifact_num = float(artifact)
                abs_error = abs(active_num - artifact_num)
                rel_error = abs_error / max(abs(active_num), 1e-12)
                passed = bool(
                    np.isfinite(active_num)
                    and np.isfinite(artifact_num)
                    and np.isclose(active_num, artifact_num, rtol=1e-6, atol=1e-8)
                )
            else:
                active_num = active
                artifact_num = artifact
                abs_error = np.nan
                rel_error = np.nan
                passed = str(active).strip().lower() == str(artifact).strip().lower()
            rows.append(
                {
                    "metric": f"input::{metric}",
                    "central_nb09": active_num,
                    "deterministic_nb01_08": artifact_num,
                    "absolute_error": abs_error,
                    "relative_error": rel_error,
                    "status": "pass" if passed else "stale_upstream_artifact",
                    "source": str(source),
                }
            )

        def record_missing(metric: str, source: Path, status: str) -> None:
            rows.append(
                {
                    "metric": metric,
                    "central_nb09": np.nan,
                    "deterministic_nb01_08": np.nan,
                    "absolute_error": np.nan,
                    "relative_error": np.nan,
                    "status": status,
                    "source": str(source),
                }
            )

        def record_schema(metric: str, passed: bool, source: Path) -> None:
            rows.append(
                {
                    "metric": f"schema::{metric}",
                    "central_nb09": np.nan,
                    "deterministic_nb01_08": np.nan,
                    "absolute_error": np.nan,
                    "relative_error": np.nan,
                    "status": "pass" if passed else "invalid_required_artifact",
                    "source": str(source),
                }
            )

        baseline_path = self.tab_dir / f"annual_heat_deaths_baseline_current_ac_{self.slug}.csv"
        ews_anchor_path = self.tab_dir / f"annual_heat_deaths_avoided_EWS_{self.slug}.csv"
        tree_path = self.tab_dir / f"trees_benefits_25y_{self.slug}.csv"
        ews_path = self.tab_dir / f"ews_benefits_25y_{self.slug}.csv"
        cba_path = self.tab_dir / f"{self.slug}_cba_summary.json"
        required_artifacts = {
            "baseline_anchor_table": baseline_path,
            "ews_anchor_table": ews_anchor_path,
            "tree_25y_table": tree_path,
            "ews_25y_table": ews_path,
            "cba_summary": cba_path,
        }
        for artifact_name, path in required_artifacts.items():
            if not path.exists():
                record_missing(f"artifact::{artifact_name}", path, "missing_required_artifact")

        # Check the provenance of deterministic cost artifacts before using
        # them as central-point targets.  NB09 is config-first; a mismatch here
        # means NB05--NB08 must be regenerated, not that NB09 should reproduce
        # obsolete inputs.
        ac_params_path = self.int_dir / f"ac_cost_params_{self.slug}.json"
        if ac_params_path.exists():
            ac_params = _load_json(ac_params_path)
            for key in ("capex_per_user", "maint_rate", "lifetime_years", "tariff_eur_per_kwh"):
                if key in ac_params and key in self.ac_cfg:
                    record_input(f"ac.{key}", self.ac_cfg[key], ac_params[key], ac_params_path)

        tree_params_path = self.int_dir / f"tree_cost_params_{self.slug}.json"
        if tree_params_path.exists():
            tree_params = _load_json(tree_params_path)
            tree_input_pairs = {
                "trees.capex_per_index_pt_eur": (
                    self.trees_cfg.get("capex_per_index_pt_eur"),
                    tree_params.get("capex_per_index_pt"),
                ),
                "trees.capex_per_tree_eur": (
                    self.trees_cfg.get("capex_per_tree_eur"),
                    tree_params.get("capex_per_tree"),
                ),
                "trees.om_per_tree_per_year_eur": (
                    self.trees_cfg.get("om_per_tree_per_year_eur"),
                    tree_params.get("om_per_tree_yr"),
                ),
                "trees.lifetime_years": (
                    self.trees_cfg.get("lifetime_years"),
                    tree_params.get("lifetime_years"),
                ),
                "trees.ramp_years": (
                    self.trees_cfg.get("ramp_years"),
                    tree_params.get("ramp_years"),
                ),
                "trees.start_age_central_years": (
                    self.trees_cfg.get("start_age_central_years", 5),
                    tree_params.get("start_age_central"),
                ),
            }
            for metric, (active, artifact) in tree_input_pairs.items():
                if active is not None and artifact is not None:
                    record_input(metric, active, artifact, tree_params_path)

        if self.ews_params_path.exists():
            ews_params = self.ews_params
            ews_costs = ews_params.get("costs", {}) or {}
            ews_input_pairs = {
                "ews.interpretation": (
                    self.ews_cfg.get("interpretation"),
                    ews_params.get("interpretation"),
                ),
                "ews.cost_model": (
                    self.ews_cfg.get("cost_model"),
                    ews_costs.get("cost_model"),
                ),
                "ews.capex_setup": (
                    self.ews_cfg.get("capex_setup", 0.0),
                    ews_costs.get("capex_setup"),
                ),
                "ews.opex_annual_fixed": (
                    self.ews_cfg.get("opex_annual_fixed", 0.0),
                    ews_costs.get("opex_annual_fixed"),
                ),
                "ews.pavanello.usd_per_capita_per_day": (
                    (self.ews_cfg.get("pavanello", {}) or {}).get("usd_per_capita_per_day"),
                    (ews_costs.get("pavanello", {}) or {}).get("usd_per_capita_per_day"),
                ),
                "ews.pavanello.eur_usd_rate": (
                    (self.ews_cfg.get("pavanello", {}) or {}).get("eur_usd_rate"),
                    (ews_costs.get("pavanello", {}) or {}).get("eur_usd_rate"),
                ),
            }
            for metric, (active, artifact) in ews_input_pairs.items():
                if active is not None and artifact is not None:
                    record_input(metric, active, artifact, self.ews_params_path)

        if baseline_path.exists():
            baseline = pd.read_csv(baseline_path)
            value_col = (
                "deaths_overall"
                if "deaths_overall" in baseline.columns
                else "deaths_annual"
                if "deaths_annual" in baseline.columns
                else None
            )
            if value_col is None or "year" not in baseline.columns:
                record_missing("schema::baseline_anchor_table", baseline_path, "missing_required_metric")
                baseline = pd.DataFrame()
            else:
                baseline_years = pd.to_numeric(baseline["year"], errors="coerce").dropna().astype(int)
                record_schema(
                    "baseline_anchor_years",
                    len(baseline) == len(self.years)
                    and baseline_years.is_unique
                    and set(baseline_years.tolist()) == set(map(int, self.years)),
                    baseline_path,
                )
            for item in baseline.itertuples(index=False):
                year = int(getattr(item, "year"))
                if f"reference_deaths_{year}" in result:
                    record(
                        f"reference_deaths_{year}",
                        result[f"reference_deaths_{year}"],
                        getattr(item, value_col),
                        baseline_path,
                    )

        ac_gross_path = self.int_dir / f"annual_heat_deaths_climada_avoided_AC_{self.slug}.csv"
        if ac_gross_path.exists():
            ac_gross = pd.read_csv(ac_gross_path)
            value_col = "overall" if "overall" in ac_gross.columns else ac_gross.columns[-1]
            for item in ac_gross.itertuples(index=False):
                year = int(getattr(item, "year"))
                if f"gross_ac_avoided_deaths_{year}" in result:
                    record(
                        f"gross_ac_avoided_deaths_{year}",
                        result[f"gross_ac_avoided_deaths_{year}"],
                        getattr(item, value_col),
                        ac_gross_path,
                    )

        tree_anchor_path = self.int_dir / f"avoided_deaths_trees_only_{self.slug}.csv"
        if tree_anchor_path.exists():
            tree_anchor = pd.read_csv(tree_anchor_path)
            value_col = "overall" if "overall" in tree_anchor.columns else tree_anchor.columns[-1]
            for item in tree_anchor.itertuples(index=False):
                year = int(getattr(item, "year"))
                key = f"full_maturity_tree_avoided_deaths_{year}"
                if key in result:
                    record(key, result[key], getattr(item, value_col), tree_anchor_path)

        if ews_anchor_path.exists():
            ews_anchor = pd.read_csv(ews_anchor_path)
            if "scenario" in ews_anchor.columns:
                ews_anchor = ews_anchor.loc[
                    ews_anchor["scenario"].astype(str).str.lower() == "central"
                ]
            anchor_schema_ok = (
                "year" in ews_anchor.columns
                and "net_avoided_deaths" in ews_anchor.columns
                and len(ews_anchor) == len(self.years)
            )
            if anchor_schema_ok:
                anchor_years_found = pd.to_numeric(ews_anchor["year"], errors="coerce").dropna().astype(int)
                anchor_schema_ok = (
                    anchor_years_found.is_unique
                    and set(anchor_years_found.tolist()) == set(map(int, self.years))
                )
            record_schema("ews_anchor_years", bool(anchor_schema_ok), ews_anchor_path)
            if "net_avoided_deaths" in ews_anchor.columns:
                for item in ews_anchor.itertuples(index=False):
                    year = int(getattr(item, "year"))
                    key = f"ews_avoided_deaths_{year}"
                    if key in result:
                        record(
                            key,
                            result[key],
                            getattr(item, "net_avoided_deaths"),
                            ews_anchor_path,
                        )

        wh_path = self.int_dir / f"ac_wasteheat_timeseries_{self.slug}.csv"
        if wh_path.exists():
            wh = pd.read_csv(wh_path)
            wh = wh.loc[wh["year"].astype(int).between(min(self.years), min(self.years) + HORIZON_YEARS - 1)]
            penalty_col = "penalty_incremental"
            if penalty_col in wh.columns:
                for item in wh.itertuples(index=False):
                    year = int(getattr(item, "year"))
                    wh_key = f"ac_waste_heat_penalty_deaths_{year}"
                    gross_key = f"gross_ac_avoided_deaths_{year}"
                    net_key = f"ac_net_avoided_deaths_{year}"
                    if wh_key in result:
                        penalty = getattr(item, penalty_col)
                        record(wh_key, result[wh_key], penalty, wh_path)
                        if gross_key in result and net_key in result:
                            record(
                                net_key,
                                result[net_key],
                                result[gross_key] - float(penalty),
                                wh_path,
                            )
                record(
                    "ac_waste_heat_penalty_25y_cum",
                    result["ac_waste_heat_penalty_25y_cum"],
                    pd.to_numeric(wh[penalty_col], errors="coerce").sum(),
                    wh_path,
                )

        if tree_path.exists():
            trees = pd.read_csv(tree_path)
            expected_policy_years = set(range(int(min(self.years)), int(min(self.years)) + HORIZON_YEARS))
            tree_schema_ok = "year" in trees.columns and "trees_only_dynamic" in trees.columns and len(trees) == HORIZON_YEARS
            if tree_schema_ok:
                tree_years = pd.to_numeric(trees["year"], errors="coerce").dropna().astype(int)
                tree_schema_ok = tree_years.is_unique and set(tree_years.tolist()) == expected_policy_years
            record_schema("tree_25y_years", bool(tree_schema_ok), tree_path)
            if "trees_only_dynamic" in trees.columns:
                record(
                    "tree_avoided_deaths_25y_cum",
                    result["tree_avoided_deaths_25y_cum"],
                    pd.to_numeric(trees["trees_only_dynamic"], errors="coerce").sum(),
                    tree_path,
                )

        if ews_path.exists():
            ews = pd.read_csv(ews_path)
            if "scenario" in ews.columns:
                selected = ews.loc[ews["scenario"].astype(str).str.lower() == "central"]
                if not selected.empty:
                    ews = selected
            benefit_col = "net_avoided_deaths" if "net_avoided_deaths" in ews.columns else "avoided_deaths"
            expected_policy_years = set(range(int(min(self.years)), int(min(self.years)) + HORIZON_YEARS))
            ews_schema_ok = "year" in ews.columns and benefit_col in ews.columns and len(ews) == HORIZON_YEARS
            if ews_schema_ok:
                ews_years = pd.to_numeric(ews["year"], errors="coerce").dropna().astype(int)
                ews_schema_ok = ews_years.is_unique and set(ews_years.tolist()) == expected_policy_years
            record_schema("ews_25y_years", bool(ews_schema_ok), ews_path)
            if benefit_col in ews.columns:
                record(
                    "ews_net_avoided_deaths_25y_cum",
                    result["ews_net_avoided_deaths_25y_cum"],
                    pd.to_numeric(ews[benefit_col], errors="coerce").sum(),
                    ews_path,
                )
            if "cost_pv" in ews.columns:
                record(
                    "ews_pv_cost_25y",
                    result["ews_pv_cost_25y"],
                    pd.to_numeric(ews["cost_pv"], errors="coerce").sum(),
                    ews_path,
                )

        if cba_path.exists():
            cba = _load_json(cba_path)
            comparisons = {
                "ac_pv_capex_25y": cba.get("costs", {}).get("ac", {}).get("pv_capex"),
                "ac_pv_maint_25y": cba.get("costs", {}).get("ac", {}).get("pv_maint"),
                "ac_pv_elec_25y": cba.get("costs", {}).get("ac", {}).get("pv_elec"),
                "ac_pv_cost_25y": cba.get("costs", {}).get("ac", {}).get("pv_total"),
                "ac_pv_capex_with_trees_25y": cba.get("costs", {}).get("ac_with_trees_interaction", {}).get("pv_capex"),
                "ac_pv_maint_with_trees_25y": cba.get("costs", {}).get("ac_with_trees_interaction", {}).get("pv_maint"),
                "ac_pv_elec_with_trees_25y": cba.get("costs", {}).get("ac_with_trees_interaction", {}).get("pv_elec"),
                "ac_pv_cost_with_trees_25y": cba.get("costs", {}).get("ac_with_trees_interaction", {}).get("pv_total"),
                "tree_pv_capex_25y": cba.get("costs", {}).get("trees", {}).get("pv_capex"),
                "tree_pv_om_25y": cba.get("costs", {}).get("trees", {}).get("pv_om_base"),
                "tree_pv_cost_25y": cba.get("costs", {}).get("trees", {}).get("pv_total_base"),
                "ac_gross_avoided_deaths_25y_cum": cba.get("benefits", {}).get("ac", {}).get("gross_25y"),
                "ac_net_avoided_deaths_25y_cum": cba.get("benefits", {}).get("ac", {}).get("net_25y"),
                "ac_waste_heat_penalty_25y_cum_cba": cba.get("benefits", {}).get("ac", {}).get("waste_heat_penalty_25y"),
                "ac_with_trees_gross_avoided_deaths_25y_cum": cba.get("benefits", {}).get("ac_with_trees_interaction", {}).get("gross_25y"),
                "ac_with_trees_net_avoided_deaths_25y_cum": cba.get("benefits", {}).get("ac_with_trees_interaction", {}).get("net_25y"),
                "ac_with_trees_waste_heat_penalty_25y_cum": cba.get("benefits", {}).get("ac_with_trees_interaction", {}).get("waste_heat_penalty_25y"),
                "tree_avoided_deaths_25y_cum_cba": cba.get("benefits", {}).get("trees", {}).get("avoided_deaths_25y"),
                "tree_on_top_of_ac_avoided_deaths_25y_cum": cba.get("benefits", {}).get("trees", {}).get("on_top_of_ac_25y"),
                "ews_net_avoided_deaths_25y_cum_cba": cba.get("benefits", {}).get("ews", {}).get("avoided_deaths_25y"),
            }
            aliases = {
                "ac_waste_heat_penalty_25y_cum_cba": "ac_waste_heat_penalty_25y_cum",
                "tree_avoided_deaths_25y_cum_cba": "tree_avoided_deaths_25y_cum",
                "ews_net_avoided_deaths_25y_cum_cba": "ews_net_avoided_deaths_25y_cum",
                "ac_pv_capex_with_trees_25y": "ac_pv_capex_25y",
                "ac_pv_maint_with_trees_25y": "ac_pv_maint_25y",
            }
            for metric, expected in comparisons.items():
                result_key = aliases.get(metric, metric)
                if expected is not None and result_key in result:
                    record(metric, result[result_key], expected, cba_path)

            tree_top = cba.get("benefits", {}).get("trees", {}).get("on_top_of_ac_25y")
            ac_tree_net = cba.get("benefits", {}).get("ac_with_trees_interaction", {}).get("net_25y")
            ac_tree_cost = cba.get("costs", {}).get("ac_with_trees_interaction", {}).get("pv_total")
            tree_cost = cba.get("costs", {}).get("trees", {}).get("pv_total_base")
            if tree_top is not None and ac_tree_net is not None:
                record(
                    "combined_ac_tree_net_avoided_deaths_25y_cum",
                    result["combined_ac_tree_net_avoided_deaths_25y_cum"],
                    float(ac_tree_net) + float(tree_top),
                    cba_path,
                )
            if ac_tree_cost is not None and tree_cost is not None:
                record(
                    "combined_ac_tree_pv_cost_25y",
                    result["combined_ac_tree_pv_cost_25y"],
                    float(ac_tree_cost) + float(tree_cost),
                    cba_path,
                )

        required_metrics = {
            "schema::baseline_anchor_years",
            "schema::ews_anchor_years",
            "schema::tree_25y_years",
            "schema::ews_25y_years",
            *(f"reference_deaths_{year}" for year in self.years),
            *(f"ews_avoided_deaths_{year}" for year in self.years),
            "tree_avoided_deaths_25y_cum",
            "ews_net_avoided_deaths_25y_cum",
            "ews_pv_cost_25y",
            "ac_pv_capex_25y",
            "ac_pv_maint_25y",
            "ac_pv_elec_25y",
            "ac_pv_cost_25y",
            "ac_pv_elec_with_trees_25y",
            "ac_pv_cost_with_trees_25y",
            "tree_pv_capex_25y",
            "tree_pv_om_25y",
            "tree_pv_cost_25y",
            "ac_gross_avoided_deaths_25y_cum",
            "ac_net_avoided_deaths_25y_cum",
            "ac_waste_heat_penalty_25y_cum_cba",
            "ac_with_trees_gross_avoided_deaths_25y_cum",
            "ac_with_trees_net_avoided_deaths_25y_cum",
            "ac_with_trees_waste_heat_penalty_25y_cum",
            "tree_on_top_of_ac_avoided_deaths_25y_cum",
            "combined_ac_tree_net_avoided_deaths_25y_cum",
            "combined_ac_tree_pv_cost_25y",
        }
        recorded_metrics = {str(row["metric"]) for row in rows}
        for metric in sorted(required_metrics - recorded_metrics):
            record_missing(metric, self.out, "missing_required_metric")

        parity_path = self.unc_dir / f"central_parity_{self.slug}_improved_fast.csv"
        parity = pd.DataFrame(rows)
        if parity.empty:
            parity = pd.DataFrame(
                [{"metric": "deterministic_artifacts", "status": "unavailable", "source": str(self.out)}]
            )
        _atomic_write_csv(parity_path, parity, index=False)
        failures = parity.loc[
            parity["status"].isin(
                [
                    "mismatch",
                    "stale_upstream_artifact",
                    "missing_required_artifact",
                    "missing_required_metric",
                    "invalid_required_artifact",
                ]
            )
        ]
        if not failures.empty:
            stale = failures.loc[failures["status"] == "stale_upstream_artifact", "metric"].astype(str).tolist()
            mismatch = failures.loc[failures["status"] == "mismatch", "metric"].astype(str).tolist()
            missing = failures.loc[
                failures["status"].isin(
                    ["missing_required_artifact", "missing_required_metric", "invalid_required_artifact"]
                ),
                "metric",
            ].astype(str).tolist()
            detail_parts = []
            if stale:
                detail_parts.append("stale NB05--NB08 inputs: " + ", ".join(stale))
            if mismatch:
                detail_parts.append("central numerical mismatches: " + ", ".join(mismatch))
            if missing:
                detail_parts.append("missing required deterministic targets: " + ", ".join(missing))
            raise RuntimeError(
                f"[{self.slug}] Central NB09 parity failed ({'; '.join(detail_parts)}). "
                f"See {parity_path}."
            )
        return parity_path

    def waste_heat_dailymean_delta(self, penetration: np.ndarray, sample: dict[str, Any]) -> np.ndarray:
        """Return NB05-equivalent daily-mean waste-heat warming for a coverage path."""
        pen = np.asarray(penetration, dtype=float)
        d_t_night = np.array(
            [self.dT_night_from_penetration(float(value), sample["wh_case"]) for value in pen],
            dtype=float,
        )
        if sample["cop_enabled"]:
            cop_sens = float(self.cop_sens.get(sample["cop_case"], self.cop_sens.get("central", 0.065)))
            amp = np.array(
                [self.cop_amplification_factor(float(value), cop_sens) for value in d_t_night],
                dtype=float,
            )
            d_t_night = d_t_night * amp
        return np.maximum(float(sample["WH_RATIO"]) * d_t_night, 0.0)

    def compute_lambda_y(
        self,
        sample: dict[str, Any],
        reference_anchor_results: dict[int, dict[str, Any]],
        years_all: np.ndarray,
    ) -> np.ndarray:
        """Compute the tree-induced AC-activity multiplier exactly as NB08.

        NB08 uses the NB07-exported monthly city-mean translation diagnostic
        for this second-order interaction and a simple tree-age maturity path.
        Primary tree mortality benefits do *not* use this approximation.
        """
        if self.tree_lambda_dt2m_monthly is None:
            warnings.warn(
                f"[{self.slug}] NB07 lambda_y approximation is unavailable; using lambda_y=1."
            )
            return np.ones(len(years_all), dtype=float)

        cap_ratio = float(sample["TREE_CAP_UPLIFT"]) / max(self.tree_base_cap, 1e-6)
        monthly_delta = (
            np.asarray(self.tree_lambda_dt2m_monthly, dtype=float)
            * float(sample["TREE_COEFF_SCALE"])
            * cap_ratio
        )
        anchor_ratios: list[float] = []
        for year in self.years:
            base_t = np.asarray(reference_anchor_results[year]["hazard_citymean_daily"], dtype=float)
            months = np.asarray(self.months_by_year[year], dtype=int)
            years_since = int(year) - int(years_all[0])
            maturity = float(
                np.clip(
                    (years_since + int(sample["tree_start_age"])) / max(int(sample["tree_ramp_years"]), 1),
                    0.0,
                    1.0,
                )
            )
            tree_t = base_t + monthly_delta[months - 1] * maturity
            s_base = self.waste_heat_activity_share(base_t)
            s_tree = self.waste_heat_activity_share(tree_t)
            ratio = 1.0 if s_base <= 1e-12 else s_tree / s_base
            anchor_ratios.append(float(np.clip(ratio, 0.0, 1.0)))
        return np.clip(
            _interp_1d_years(
                np.asarray(self.years, dtype=int),
                np.asarray(anchor_ratios, dtype=float),
                np.asarray(years_all, dtype=int),
            ),
            0.0,
            1.0,
        )

    def evaluate_sample(self, raw_row: pd.Series) -> dict[str, Any]:
        sample = self.sample_dict(raw_row)
        extreme_threshold_c = None
        extreme_min_duration = None
        if self.ews_uses_event_mask_warning():
            extreme_threshold_c = self.extreme_threshold_from_percentile(
                baseline_mode=sample["baseline_mode"],
                percentile=sample["extreme_threshold_pct"],
            )
            extreme_min_duration = int(sample["extreme_min_duration_days"])

        # Compute explicit, mutually interpretable policy branches.  Waste heat
        # is an aggregate incremental mortality penalty in NB05/NB08, not a
        # temperature field injected into either spatial branch.
        ref_anchor_results, ref_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            tree_enabled=False,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ac_gross_anchor_results, ac_gross_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="policy",
            tree_enabled=False,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        tree_anchor_results, tree_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            tree_enabled=True,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ac_tree_anchor_results, ac_tree_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="policy",
            tree_enabled=True,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )

        # NB05 derives the marginal deaths per +1 C separately under current
        # and policy AC.  These branches are required for the aggregate
        # incremental waste-heat externality.
        ref_plus1_anchor_results, _, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            tree_enabled=False,
            ews_enabled=False,
            temperature_offset_c=1.0,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ac_plus1_anchor_results, _, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="policy",
            tree_enabled=False,
            ews_enabled=False,
            temperature_offset_c=1.0,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )

        # Standalone EWS is always evaluated against the common reference
        # branch.  Combined-policy EWS outputs can be added explicitly later;
        # they must never replace the standalone EWS headline.
        ews_policy_anchor_results, ews_policy_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            tree_enabled=False,
            ews_enabled=True,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
            base_anchor_results=ref_anchor_results,
            base_pop_totals=ref_pop_totals,
        )

        sample_year = int(sample["year"])
        ref_year_res = ref_anchor_results[sample_year]
        ews_year_res = ews_policy_anchor_results[sample_year]
        reference_daily = np.asarray(ref_year_res["daily_residual_total"], dtype=float)
        impact_freq = _daily_quantiles(reference_daily, DAILY_QUANTILE_PCTS)
        annual_deaths = float(reference_daily.sum())
        aai_agg = annual_deaths

        anchor_years = np.array(self.years, dtype=int)
        years_all = np.arange(min(self.years), min(self.years) + HORIZON_YEARS, dtype=int)
        t_index = years_all - years_all[0]

        warning_days_anchor = np.array([ews_policy_anchor_results[y]["warning_days"] for y in self.years], dtype=float)
        deaths_warning_anchor = np.array([ews_policy_anchor_results[y]["deaths_on_warning_days"] for y in self.years], dtype=float)
        pop_anchor = np.array([ref_pop_totals[y] for y in self.years], dtype=float)

        warning_days_25y = _interp_1d_years(anchor_years, warning_days_anchor, years_all)
        deaths_warning_25y = _interp_1d_years(anchor_years, deaths_warning_anchor, years_all)
        pop_25y = _interp_1d_years(anchor_years, pop_anchor, years_all)

        # Reconstruct the annual EWS benefit exactly as NB06 does.  NB06
        # interpolates the warning-day death burden by age and city-mean AC
        # coverage separately, then reapplies efficacy, overlap, displacement
        # and the step ramp for every policy year.  Directly interpolating the
        # already-combined anchor-year benefit is not equivalent because the
        # AC-overlap term multiplies the interpolated death burden.
        ramp_25y = np.array([self.ramp_factor(y, sample["ews_ramp_years"]) for y in years_all], dtype=float)
        ews_ac_coverage_anchor = np.array(
            [self.coverage_mean_for_ews(y, sample["ac_ssp"], mode="base") for y in self.years],
            dtype=float,
        )
        ews_ac_coverage_25y = _interp_1d_years(anchor_years, ews_ac_coverage_anchor, years_all)
        overlap = float(
            self.ews_overlap.get(
                sample["ews_overlap_level"],
                self.ews_overlap.get("central", 0.3),
            )
        )
        ews_ac_penalty_25y = np.clip(1.0 - overlap * ews_ac_coverage_25y, 0.0, 1.0)
        gross_25y = np.zeros_like(years_all, dtype=float)
        net_25y = np.zeros_like(years_all, dtype=float)
        lys_25y = np.zeros_like(years_all, dtype=float)
        for age in AGE_ORDER:
            deaths_age_anchor = np.array(
                [ews_policy_anchor_results[y]["deaths_warning_by_age"][age] for y in self.years],
                dtype=float,
            )
            deaths_age_25y = _interp_1d_years(anchor_years, deaths_age_anchor, years_all)
            interpretation = str(sample["ews_interpretation"]).lower()
            if interpretation == "marginal":
                level = sample[f"ews_eff_{self._age_key(age)}_level"]
                efficacy = float(
                    self.ews_marg.get(age, {}).get(
                        level,
                        self.ews_marg.get(age, {}).get("central", 0.0),
                    )
                )
            elif interpretation == "intermediate":
                level = sample[f"ews_eff_{self._age_key(age)}_level"]
                efficacy_marginal = float(
                    self.ews_marg.get(age, {}).get(
                        level,
                        self.ews_marg.get(age, {}).get("central", 0.0),
                    )
                )
                efficacy_counterfactual = float(
                    self.ews_cf.get(
                        sample["ews_cf_eff_level"],
                        self.ews_cf.get("central", 0.0),
                    )
                )
                efficacy = 0.5 * efficacy_marginal + 0.5 * efficacy_counterfactual
            else:
                efficacy = float(
                    self.ews_cf.get(
                        sample["ews_cf_eff_level"],
                        self.ews_cf.get("central", 0.0),
                    )
                )
            displacement_level = sample[f"ews_disp_{self._age_key(age)}_level"]
            displacement = float(
                self.ews_disp.get(age, {}).get(
                    displacement_level,
                    self.ews_disp.get(age, {}).get("central", 0.0),
                )
            )
            gross_age_25y = deaths_age_25y * efficacy * ramp_25y * ews_ac_penalty_25y
            net_age_25y = gross_age_25y * (1.0 - displacement)
            gross_25y += gross_age_25y
            net_25y += net_age_25y
            lys_25y += net_age_25y * float(self.ews_rly.get(age, 10))

        cost_ramp_25y = ramp_25y if bool(self.ews_cfg.get("cost_ramp_with_efficacy", False)) else np.ones_like(ramp_25y)
        pop_ref = float(np.interp(self.ews_threshold_ref_year, anchor_years.astype(float), pop_anchor))
        discount_rate = float(sample["discount_rate"])

        capex = float(self.ews_cfg.get("capex_setup", 0.0))
        opex_fixed = float(self.ews_cfg.get("opex_annual_fixed", 0.0))
        if sample["ews_cost_model"] == "pavanello":
            pav = self.ews_cfg.get("pavanello", {})
            usd_rate = float(pav.get("usd_per_capita_per_day", 0.014))
            eur_usd = float(pav.get("eur_usd_rate", 0.92))
            cost_opex_var_25y = usd_rate * eur_usd * pop_25y * warning_days_25y * cost_ramp_25y
        else:
            chi = self.ews_cfg.get("chiabai", {})
            opex_per_day = float(
                chi.get(
                    "opex_per_warning_day_incremental",
                    chi.get("opex_per_warning_day_enhanced", 14000) - chi.get("opex_per_warning_day_basic", 7800),
                )
            )
            if bool(self.ews_cfg.get("cost_scale_with_population", False)) and pop_ref > 0:
                cost_opex_var_25y = opex_per_day * (pop_25y / pop_ref) * warning_days_25y * cost_ramp_25y
            else:
                cost_opex_var_25y = opex_per_day * warning_days_25y * cost_ramp_25y

        cost_capex_25y = np.zeros_like(years_all, dtype=float)
        cost_capex_25y[0] = capex
        cost_opex_fixed_25y = opex_fixed * cost_ramp_25y
        cost_total_25y = cost_capex_25y + cost_opex_fixed_25y + cost_opex_var_25y
        discount_factors = (1.0 + discount_rate) ** t_index
        cost_pv_25y = cost_total_25y / discount_factors
        net_pv_25y = net_25y / discount_factors
        lys_pv_25y = lys_25y / discount_factors

        pv_cost_total = float(cost_pv_25y.sum())
        net_avoided_cum = float(net_25y.sum())
        net_avoided_pv = float(net_pv_25y.sum())
        lys_cum = float(lys_25y.sum())
        lys_pv = float(lys_pv_25y.sum())

        result = {
            "aai_agg": aai_agg,
            "annual_deaths": annual_deaths,
            "reference_annual_deaths": annual_deaths,
            **impact_freq,
            "sample_warning_days": float(ews_year_res["warning_days"]),
            "sample_threshold_deaths_per_day": float(ews_year_res["threshold"]),
            "sample_deaths_on_warning_days": float(ews_year_res["deaths_on_warning_days"]),
            "sample_extreme_threshold_pct": float(sample.get("extreme_threshold_pct", np.nan)),
            "sample_extreme_threshold_degC": float(extreme_threshold_c) if extreme_threshold_c is not None else np.nan,
            "sample_extreme_min_duration_days": float(extreme_min_duration) if extreme_min_duration is not None else np.nan,
            "ews_pv_cost_25y": pv_cost_total,
            "ews_net_avoided_deaths_25y_cum": net_avoided_cum,
            "ews_net_avoided_deaths_25y_pv": net_avoided_pv,
            "ews_gross_avoided_deaths_25y_cum": float(gross_25y.sum()),
            "ews_life_years_saved_25y_cum": lys_cum,
            "ews_life_years_saved_25y_pv": lys_pv,
            "ews_cost_per_net_death_25y_cum": pv_cost_total / net_avoided_cum if net_avoided_cum > 0 else np.inf,
            "ews_cost_per_net_death_25y_pv": pv_cost_total / net_avoided_pv if net_avoided_pv > 0 else np.inf,
            "ews_cost_model": sample["ews_cost_model"],
        }
        result["_ews_cost_streams"] = {
            "capex": cost_capex_25y,
            "opex_fixed": cost_opex_fixed_25y,
            "opex_variable": cost_opex_var_25y,
            "total": cost_total_25y,
            "pv_total": cost_pv_25y,
        }

        _, ref_annual_25y, ref_pop_25y, _ = self.interpolate_branch_annuals(ref_anchor_results, ref_pop_totals)
        _, ac_gross_annual_25y, _, _ = self.interpolate_branch_annuals(ac_gross_anchor_results, ac_gross_pop_totals)
        _, tree_full_annual_25y, _, _ = self.interpolate_branch_annuals(tree_anchor_results, tree_pop_totals)
        _, ac_tree_full_annual_25y, _, _ = self.interpolate_branch_annuals(ac_tree_anchor_results, ac_tree_pop_totals)
        # Use the exact NB06 25-year reconstruction above for the annual EWS
        # policy branch; anchor interpolation alone would lose the nonlinear
        # death-burden x AC-overlap interaction.
        ews_policy_annual_25y = ref_annual_25y - net_25y
        _, ref_plus1_annual_25y, _, _ = self.interpolate_branch_annuals(ref_plus1_anchor_results, ref_pop_totals)
        _, ac_plus1_annual_25y, _, _ = self.interpolate_branch_annuals(ac_plus1_anchor_results, ac_gross_pop_totals)

        ac_gross_avoided_25y = ref_annual_25y - ac_gross_annual_25y
        tree_only_raw_25y = ref_annual_25y - tree_full_annual_25y
        trees_on_top_raw_25y = ac_gross_annual_25y - ac_tree_full_annual_25y
        tree_maturity_25y = _cohort_rollout_maturity_factor(
            HORIZON_YEARS,
            int(sample["tree_ramp_years"]),
            start_age_years=int(sample["tree_start_age"]),
            lifetime_years=int(self.cfg.get("trees", {}).get("lifetime_years", HORIZON_YEARS)),
        )
        tree_avoided_25y = tree_only_raw_25y * tree_maturity_25y
        trees_on_top_25y = trees_on_top_raw_25y * tree_maturity_25y
        tree_annual_25y = ref_annual_25y - tree_avoided_25y
        ac_tree_gross_annual_25y = ac_gross_annual_25y - trees_on_top_25y
        ac_tree_gross_avoided_25y = ref_annual_25y - ac_tree_gross_annual_25y
        ews_reference_avoided_25y = ref_annual_25y - ews_policy_annual_25y

        # NB05 aggregate waste-heat accounting: activity-weighted marginal
        # deaths under current and policy AC, multiplied by each branch's
        # daily-mean warming.  The policy externality is the difference.
        activity_anchor = np.array(
            [self.waste_heat_activity_share(ref_anchor_results[y]["hazard_citymean_daily"]) for y in self.years],
            dtype=float,
        )
        activity_25y = _interp_1d_years(anchor_years, activity_anchor, years_all)
        marginal_current_25y = ref_plus1_annual_25y - ref_annual_25y
        marginal_policy_25y = ac_plus1_annual_25y - ac_gross_annual_25y
        pen_current_25y = self.coverage_series_for_waste_heat(
            years_all, sample["ac_ssp"], mode="base"
        )
        pen_policy_25y = self.coverage_series_for_waste_heat(
            years_all, sample["ac_ssp"], mode="policy"
        )
        d_t_current_25y = self.waste_heat_dailymean_delta(pen_current_25y, sample)
        d_t_policy_25y = self.waste_heat_dailymean_delta(pen_policy_25y, sample)
        if sample["wh_enabled"]:
            wh_penalty_current_25y = activity_25y * marginal_current_25y * d_t_current_25y
            wh_penalty_policy_25y = activity_25y * marginal_policy_25y * d_t_policy_25y
            ac_penalty_raw_25y = wh_penalty_policy_25y - wh_penalty_current_25y
        else:
            wh_penalty_current_25y = np.zeros_like(ref_annual_25y)
            wh_penalty_policy_25y = np.zeros_like(ref_annual_25y)
            ac_penalty_raw_25y = np.zeros_like(ref_annual_25y)

        ac_net_avoided_25y = ac_gross_avoided_25y - ac_penalty_raw_25y
        ac_net_annual_25y = ref_annual_25y - ac_net_avoided_25y

        # Lambda_y: tree-cooled AC utilization scaling for the explicit
        # AC+trees interaction branch.
        lambda_y_25y = self.compute_lambda_y(
            sample,
            ref_anchor_results,
            years_all,
        )
        ac_penalty_with_trees_25y = ac_penalty_raw_25y * lambda_y_25y
        ac_feedback_with_trees_net_avoided_25y = ac_gross_avoided_25y - ac_penalty_with_trees_25y
        ac_feedback_with_trees_net_annual_25y = ref_annual_25y - ac_feedback_with_trees_net_avoided_25y
        ac_tree_net_avoided_25y = ac_tree_gross_avoided_25y - ac_penalty_with_trees_25y
        ac_tree_net_annual_25y = ref_annual_25y - ac_tree_net_avoided_25y

        # AC costs: standalone AC plus explicit AC+trees interaction on electricity costs.
        # AC expenditure follows Notebook 08's distinct population path
        # (2020 direct/baseline + selected SSP anchors).  It intentionally
        # differs from NB06's four-anchor population interpolation above.
        ac_cost_pop_25y = self.ac_cba_population_series(years_all, sample)
        ac_costs = self.compute_ac_cost_metrics(sample, years_all, ac_cost_pop_25y)
        tree_costs = self.compute_tree_cost_metrics(sample)
        ac_gross_avoided_cum = float(ac_gross_avoided_25y.sum())
        ac_net_avoided_cum = float(ac_net_avoided_25y.sum())
        ac_net_with_trees_cum = float(ac_feedback_with_trees_net_avoided_25y.sum())
        combined_ac_tree_net_cum = float(ac_tree_net_avoided_25y.sum())
        tree_avoided_cum = float(tree_avoided_25y.sum())

        sample_ref_annual = float(np.asarray(ref_anchor_results[sample_year]["daily_residual_total"]).sum())
        sample_ac_gross_annual = float(np.asarray(ac_gross_anchor_results[sample_year]["daily_residual_total"]).sum())
        sample_tree_full_annual = float(np.asarray(tree_anchor_results[sample_year]["daily_residual_total"]).sum())
        sample_ac_tree_full_annual = float(np.asarray(ac_tree_anchor_results[sample_year]["daily_residual_total"]).sum())
        sample_ews_annual = float(np.asarray(ews_policy_anchor_results[sample_year]["daily_residual_total"]).sum())
        sample_marginal_current = (
            float(np.asarray(ref_plus1_anchor_results[sample_year]["daily_residual_total"]).sum())
            - sample_ref_annual
        )
        sample_marginal_policy = (
            float(np.asarray(ac_plus1_anchor_results[sample_year]["daily_residual_total"]).sum())
            - sample_ac_gross_annual
        )
        sample_activity = self.waste_heat_activity_share(
            ref_anchor_results[sample_year]["hazard_citymean_daily"]
        )
        sample_d_t_current = float(
            self.waste_heat_dailymean_delta(
                np.array([self.coverage_mean_for_waste_heat(sample_year, sample["ac_ssp"], mode="base")]), sample
            )[0]
        )
        sample_d_t_policy = float(
            self.waste_heat_dailymean_delta(
                np.array([self.coverage_mean_for_waste_heat(sample_year, sample["ac_ssp"], mode="policy")]), sample
            )[0]
        )
        sample_wh_penalty = 0.0
        if sample["wh_enabled"]:
            sample_wh_penalty = sample_activity * (
                sample_marginal_policy * sample_d_t_policy
                - sample_marginal_current * sample_d_t_current
            )
        result.update(
            {
                "ac_gross_annual_deaths": sample_ac_gross_annual,
                "ac_net_annual_deaths": sample_ac_gross_annual + sample_wh_penalty,
                "tree_full_maturity_annual_deaths": sample_tree_full_annual,
                "ews_annual_deaths": sample_ews_annual,
                "ac_tree_full_maturity_gross_annual_deaths": sample_ac_tree_full_annual,
                "sample_ac_waste_heat_penalty_deaths": sample_wh_penalty,
            }
        )
        # Direct anchor-year outputs remain available even when an anchor lies
        # outside the 2020--2044 CBA horizon (notably 2050).
        for year in self.years:
            ref_a = float(np.asarray(ref_anchor_results[year]["daily_residual_total"], dtype=float).sum())
            ac_a = float(np.asarray(ac_gross_anchor_results[year]["daily_residual_total"], dtype=float).sum())
            tree_full_a = float(np.asarray(tree_anchor_results[year]["daily_residual_total"], dtype=float).sum())
            ews_a = float(np.asarray(ews_policy_anchor_results[year]["daily_residual_total"], dtype=float).sum())
            ref_plus1_a = float(
                np.asarray(ref_plus1_anchor_results[year]["daily_residual_total"], dtype=float).sum()
            )
            ac_plus1_a = float(
                np.asarray(ac_plus1_anchor_results[year]["daily_residual_total"], dtype=float).sum()
            )
            wh_penalty_a = 0.0
            if sample["wh_enabled"]:
                activity_a = self.waste_heat_activity_share(
                    ref_anchor_results[year]["hazard_citymean_daily"]
                )
                d_t_current_a = float(
                    self.waste_heat_dailymean_delta(
                        np.asarray(
                            [self.coverage_mean_for_waste_heat(year, sample["ac_ssp"], mode="base")]
                        ),
                        sample,
                    )[0]
                )
                d_t_policy_a = float(
                    self.waste_heat_dailymean_delta(
                        np.asarray(
                            [self.coverage_mean_for_waste_heat(year, sample["ac_ssp"], mode="policy")]
                        ),
                        sample,
                    )[0]
                )
                wh_penalty_a = activity_a * (
                    (ac_plus1_a - ac_a) * d_t_policy_a
                    - (ref_plus1_a - ref_a) * d_t_current_a
                )
            result[f"reference_deaths_{year}"] = ref_a
            result[f"ac_gross_deaths_{year}"] = ac_a
            result[f"ac_net_deaths_{year}"] = ac_a + wh_penalty_a
            result[f"tree_full_maturity_deaths_{year}"] = tree_full_a
            result[f"ews_deaths_{year}"] = ews_a
            result[f"gross_ac_avoided_deaths_{year}"] = ref_a - ac_a
            result[f"ac_waste_heat_penalty_deaths_{year}"] = wh_penalty_a
            result[f"ac_net_avoided_deaths_{year}"] = ref_a - ac_a - wh_penalty_a
            result[f"full_maturity_tree_avoided_deaths_{year}"] = ref_a - tree_full_a
            result[f"ews_avoided_deaths_{year}"] = ref_a - ews_a

        result.update(ac_costs)
        result.update(
            {
                "reference_deaths_25y_cum": float(ref_annual_25y.sum()),
                "ac_gross_branch_deaths_25y_cum": float(ac_gross_annual_25y.sum()),
                "ac_net_branch_deaths_25y_cum": float(ac_net_annual_25y.sum()),
                "ac_gross_avoided_deaths_25y_cum": ac_gross_avoided_cum,
                "ac_net_avoided_deaths_25y_cum": ac_net_avoided_cum,
                "ac_waste_heat_penalty_25y_cum": float(ac_penalty_raw_25y.sum()),
                "ac_waste_heat_penalty_raw_25y_cum": float(ac_penalty_raw_25y.sum()),
                "ac_waste_heat_current_deaths_25y_cum": float(wh_penalty_current_25y.sum()),
                "ac_waste_heat_policy_deaths_25y_cum": float(wh_penalty_policy_25y.sum()),
                "ac_cost_per_gross_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_25y"], ac_gross_avoided_cum),
                "ac_cost_per_net_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_25y"], ac_net_avoided_cum),
                "ac_with_trees_net_avoided_deaths_25y_cum": ac_net_with_trees_cum,
                "ac_with_trees_gross_avoided_deaths_25y_cum": ac_gross_avoided_cum,
                "ac_with_trees_gross_branch_deaths_25y_cum": float(ac_gross_annual_25y.sum()),
                "ac_with_trees_net_branch_deaths_25y_cum": float(ac_feedback_with_trees_net_annual_25y.sum()),
                "ac_with_trees_waste_heat_penalty_25y_cum": float(ac_penalty_with_trees_25y.sum()),
                "ac_with_trees_cost_per_gross_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_with_trees_25y"], ac_gross_avoided_cum),
                "ac_with_trees_cost_per_net_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_with_trees_25y"], ac_net_with_trees_cum),
                "combined_ac_tree_gross_avoided_deaths_25y_cum": float(ac_tree_gross_avoided_25y.sum()),
                "combined_ac_tree_net_avoided_deaths_25y_cum": combined_ac_tree_net_cum,
                "combined_ac_tree_gross_branch_deaths_25y_cum": float(ac_tree_gross_annual_25y.sum()),
                "combined_ac_tree_net_branch_deaths_25y_cum": float(ac_tree_net_annual_25y.sum()),
                "combined_ac_tree_pv_cost_25y": float(ac_costs["ac_pv_cost_with_trees_25y"] + tree_costs["tree_pv_cost_25y"]),
                "combined_ac_tree_cost_per_net_death_25y_cum": _safe_ratio(
                    ac_costs["ac_pv_cost_with_trees_25y"] + tree_costs["tree_pv_cost_25y"],
                    combined_ac_tree_net_cum,
                ),
                "lambda_y_mean": float(lambda_y_25y.mean()),
            }
        )
        result.update(tree_costs)
        result.update(
            {
                "tree_avoided_deaths_25y_cum": tree_avoided_cum,
                "tree_raw_full_maturity_avoided_deaths_25y_cum": float(tree_only_raw_25y.sum()),
                "tree_on_top_of_ac_avoided_deaths_25y_cum": float(trees_on_top_25y.sum()),
                "tree_on_top_of_ac_raw_full_maturity_avoided_deaths_25y_cum": float(trees_on_top_raw_25y.sum()),
                "tree_branch_deaths_25y_cum": float(tree_annual_25y.sum()),
                "tree_cost_per_death_25y_cum": _safe_ratio(tree_costs["tree_pv_cost_25y"], tree_avoided_cum),
            }
        )
        result["ews_branch_deaths_25y_cum"] = float(ews_policy_annual_25y.sum())

        # Vegetation-electricity outputs are reported as explicit tree co-benefits, not netted into tree CBA by default.
        result["tree_elec_cost_coverage_base_users_pct"] = (
            100.0 * ac_costs.get("tree_elec_pv_savings_base_users", 0.0) / max(tree_costs["tree_pv_cost_25y"], 1e-6)
        )
        result["tree_elec_cost_coverage_all_users_pct"] = (
            100.0 * ac_costs.get("tree_elec_pv_savings_all_users", 0.0) / max(tree_costs["tree_pv_cost_25y"], 1e-6)
        )
        result["elec_feedback_enabled"] = sample.get("elec_feedback_enabled", False)
        result["elec_coeff_scale"] = sample.get("elec_coeff_scale", 1.0)

        result["_policy_branch_annuals"] = {
            "reference": ref_annual_25y,
            "ac_policy_gross": ac_gross_annual_25y,
            "ac_policy_net": ac_net_annual_25y,
            "ac_policy_net_with_tree_feedback": ac_feedback_with_trees_net_annual_25y,
            "tree_policy": tree_annual_25y,
            "ews_policy": ews_policy_annual_25y,
            "ac_tree_policy_gross": ac_tree_gross_annual_25y,
            "ac_tree_policy_net": ac_tree_net_annual_25y,
        }
        result["_policy_branch_effects"] = {
            "ac_gross_avoided_25y": ac_gross_avoided_25y,
            "ac_net_avoided_25y": ac_net_avoided_25y,
            "ac_net_with_tree_feedback_25y": ac_feedback_with_trees_net_avoided_25y,
            "ac_tree_gross_avoided_25y": ac_tree_gross_avoided_25y,
            "ac_net_with_trees_25y": ac_tree_net_avoided_25y,
            "ac_penalty_raw_25y": ac_penalty_raw_25y,
            "ac_penalty_with_trees_25y": ac_penalty_with_trees_25y,
            "tree_only_raw_25y": tree_only_raw_25y,
            "trees_on_top_raw_25y": trees_on_top_raw_25y,
            "tree_maturity_25y": tree_maturity_25y,
            "tree_avoided_25y": tree_avoided_25y,
            "trees_on_top_25y": trees_on_top_25y,
            "ews_reference_avoided_25y": ews_reference_avoided_25y,
            "lambda_y_25y": lambda_y_25y,
        }

        # ── Vulnerability output metrics (Level A: does not affect mortality) ──
        exp_ssp = self.exp_ssp_options[int(sample["EXP_SSP_IDX"])] if self.exp_ssp_options else None
        anchor_yr = int(self.vuln_cfg.get("dynamic", {}).get("drmkc", {}).get("anchor_year", 2030))
        vuln_scen = exp_ssp if sample_year > anchor_yr else None
        pop_key = (vuln_scen, sample_year) if vuln_scen else (None, sample_year)
        vuln_met = self.compute_vulnerability_metrics(
            sample_year, vuln_scen, sample,
            pop_grid=self.vuln_pop_grids.get(pop_key),
        )
        for k, v in vuln_met.items():
            result[f"vuln_{k}"] = v

        # Fixed 2050 horizon vulnerability
        vuln_2050_scen = exp_ssp  # 2050 > anchor_year → always scenario-dependent
        pop_key_2050 = (vuln_2050_scen, 2050) if vuln_2050_scen else (None, 2050)
        vuln_2050 = self.compute_vulnerability_metrics(
            2050, vuln_2050_scen, sample,
            pop_grid=self.vuln_pop_grids.get(pop_key_2050),
        )
        for k, v in vuln_2050.items():
            result[f"vuln_2050_{k}"] = v

        result["_anchor_years"] = anchor_years
        result["_anchor_results"] = ref_anchor_results
        result["_anchor_results_ews"] = ews_policy_anchor_results
        result["_years_all"] = years_all
        result["_warning_days_25y"] = warning_days_25y
        result["_deaths_warning_25y"] = deaths_warning_25y
        result["_net_25y"] = net_25y
        result["_cost_pv_25y"] = cost_pv_25y
        return result

    def run_central_control(self) -> dict[str, Path | None]:
        """Evaluate and validate the unsampled canonical NB01--NB08 point."""
        central_path = self.unc_dir / f"central_configuration_{self.slug}_improved_fast.csv"
        central_qa_path = self.unc_dir / f"central_mathematical_qa_{self.slug}_improved_fast.csv"
        parity_path = self.unc_dir / f"central_parity_{self.slug}_improved_fast.csv"
        marker_path = self.unc_dir / "CENTRAL_CONTROL_COMPLETE.json"
        campaign_signature = self.run_provenance["campaign_signature"]

        if marker_path.exists():
            marker = _load_json(marker_path)
            expected_paths: dict[str, Path] = {
                "central": central_path,
                "central_mathematical_qa": central_qa_path,
            }
            if getattr(self, "_lhs_scope", None) != "burke_sensitivity":
                expected_paths["central_parity"] = parity_path
            expected_hashes = marker.get("output_sha256", {})
            reusable = bool(
                marker.get("output_schema_version") == OUTPUT_SCHEMA_VERSION
                and marker.get("campaign_signature") == campaign_signature
                and all(
                    path.is_file() and expected_hashes.get(key) == _sha256_file(path)
                    for key, path in expected_paths.items()
                )
            )
            if not reusable:
                raise RuntimeError(
                    f"[{self.slug}] Existing central-control marker is inconsistent with its outputs: "
                    f"{marker_path}. Use a new NB09_CAMPAIGN_ID rather than mixing controls."
                )
            self.central_df = pd.read_csv(central_path)
            self.central_parity_path = expected_paths.get("central_parity")
            print(f"[{self.slug}] reusing verified central NB01--NB08 parity control")
            return {
                "central": central_path,
                "central_mathematical_qa": central_qa_path,
                "central_parity": self.central_parity_path,
                "central_control_completion": marker_path,
            }

        central_raw = self.central_parameter_row()
        central_out = self.evaluate_sample(central_raw)
        central_decoded = self.sample_dict(central_raw)
        central_qa = self.validate_sample_output(-1, central_decoded, central_out)
        central_record = {
            **central_raw.to_dict(),
            **{k: v for k, v in central_decoded.items() if k not in central_raw.index},
            **{k: v for k, v in central_out.items() if not str(k).startswith("_")},
        }
        self.central_df = pd.DataFrame([central_record])
        _atomic_write_csv(central_path, self.central_df, index=False)
        _atomic_write_csv(central_qa_path, pd.DataFrame(central_qa), index=False)
        self.central_parity_path = None
        if getattr(self, "_lhs_scope", None) != "burke_sensitivity":
            self.central_parity_path = self.validate_central_against_deterministic(central_out)
        control_paths = {
            "central": central_path,
            "central_mathematical_qa": central_qa_path,
        }
        if self.central_parity_path is not None:
            control_paths["central_parity"] = self.central_parity_path
        _atomic_write_json(
            marker_path,
            {
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
                "campaign_id": self.run_provenance["campaign_id"],
                "campaign_signature": campaign_signature,
                "city": self.city,
                "slug": self.slug,
                "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "output_sha256": {
                    key: _sha256_file(path)
                    for key, path in control_paths.items()
                },
            },
        )
        return {
            "central": central_path,
            "central_mathematical_qa": central_qa_path,
            "central_parity": self.central_parity_path,
            "central_control_completion": marker_path,
        }

    def _records_for_sample(
        self,
        sample_idx: int,
        raw_row: pd.Series,
        out: dict[str, Any],
    ) -> dict[str, Any]:
        """Build every persisted record for one evaluated LHS row."""
        raw = raw_row.to_dict()
        decoded = self.sample_dict(raw_row)
        sample_row = {
            "sample_idx": int(sample_idx),
            **raw,
            **{key: value for key, value in decoded.items() if key not in raw},
            **{key: value for key, value in out.items() if not str(key).startswith("_")},
        }
        impact = {
            "sample_idx": int(sample_idx),
            "aai_agg": out["aai_agg"],
            "annual_deaths": out["annual_deaths"],
            "reference_annual_deaths": out["reference_annual_deaths"],
            "ac_gross_annual_deaths": out["ac_gross_annual_deaths"],
            "ac_net_annual_deaths": out["ac_net_annual_deaths"],
            "tree_full_maturity_annual_deaths": out["tree_full_maturity_annual_deaths"],
            "ews_annual_deaths": out["ews_annual_deaths"],
            **{
                key: value
                for key, value in out.items()
                if any(
                    key.startswith(prefix)
                    for prefix in (
                        "reference_deaths_",
                        "ac_gross_deaths_",
                        "ac_net_deaths_",
                        "tree_full_maturity_deaths_",
                        "ews_deaths_",
                        "gross_ac_avoided_deaths_",
                        "ac_net_avoided_deaths_",
                        "ac_waste_heat_penalty_deaths_",
                        "full_maturity_tree_avoided_deaths_",
                        "ews_avoided_deaths_",
                    )
                )
            },
            **{f"daily_p{p}": out[f"daily_p{p}"] for p in DAILY_QUANTILE_PCTS},
        }
        cba_ews = {
            "sample_idx": int(sample_idx),
            "reference_deaths_25y_cum": out["reference_deaths_25y_cum"],
            "ews_branch_deaths_25y_cum": out["ews_branch_deaths_25y_cum"],
            "ews_pv_cost_25y": out["ews_pv_cost_25y"],
            "ews_net_avoided_deaths_25y_cum": out["ews_net_avoided_deaths_25y_cum"],
            "ews_net_avoided_deaths_25y_pv": out["ews_net_avoided_deaths_25y_pv"],
            "ews_cost_per_net_death_25y_cum": out["ews_cost_per_net_death_25y_cum"],
            "ews_cost_per_net_death_25y_pv": out["ews_cost_per_net_death_25y_pv"],
            "ews_life_years_saved_25y_cum": out["ews_life_years_saved_25y_cum"],
        }
        cba_ac = {
            "sample_idx": int(sample_idx),
            **{
                key: out[key]
                for key in (
                    "reference_deaths_25y_cum",
                    "ac_gross_branch_deaths_25y_cum",
                    "ac_net_branch_deaths_25y_cum",
                    "ac_pv_capex_25y",
                    "ac_pv_maint_25y",
                    "ac_pv_elec_25y",
                    "ac_pv_elec_with_trees_25y",
                    "ac_pv_cost_25y",
                    "ac_pv_cost_with_trees_25y",
                    "ac_added_users_final",
                    "ac_gross_avoided_deaths_25y_cum",
                    "ac_net_avoided_deaths_25y_cum",
                    "ac_waste_heat_penalty_25y_cum",
                    "ac_waste_heat_current_deaths_25y_cum",
                    "ac_waste_heat_policy_deaths_25y_cum",
                    "ac_cost_per_gross_death_25y_cum",
                    "ac_cost_per_net_death_25y_cum",
                    "ac_with_trees_gross_branch_deaths_25y_cum",
                    "ac_with_trees_net_branch_deaths_25y_cum",
                    "ac_with_trees_gross_avoided_deaths_25y_cum",
                    "ac_with_trees_net_avoided_deaths_25y_cum",
                    "ac_with_trees_waste_heat_penalty_25y_cum",
                    "ac_with_trees_cost_per_gross_death_25y_cum",
                    "ac_with_trees_cost_per_net_death_25y_cum",
                    "combined_ac_tree_gross_branch_deaths_25y_cum",
                    "combined_ac_tree_net_branch_deaths_25y_cum",
                    "combined_ac_tree_gross_avoided_deaths_25y_cum",
                    "combined_ac_tree_net_avoided_deaths_25y_cum",
                    "combined_ac_tree_pv_cost_25y",
                    "combined_ac_tree_cost_per_net_death_25y_cum",
                )
            },
        }
        cba_trees = {
            "sample_idx": int(sample_idx),
            **{
                key: out[key]
                for key in (
                    "reference_deaths_25y_cum",
                    "tree_branch_deaths_25y_cum",
                    "tree_pv_capex_25y",
                    "tree_pv_om_25y",
                    "tree_pv_cost_25y",
                    "tree_avoided_deaths_25y_cum",
                    "tree_raw_full_maturity_avoided_deaths_25y_cum",
                    "tree_on_top_of_ac_avoided_deaths_25y_cum",
                    "tree_cost_per_death_25y_cum",
                    "tree_elec_pv_savings_base_users",
                    "tree_elec_pv_savings_all_users",
                    "tree_elec_kwh_base_users_25y",
                    "tree_elec_kwh_all_users_25y",
                    "tree_elec_co2_base_users_t_25y",
                    "tree_elec_co2_all_users_t_25y",
                    "tree_elec_cost_coverage_base_users_pct",
                    "tree_elec_cost_coverage_all_users_pct",
                    "elec_feedback_enabled",
                    "elec_coeff_scale",
                )
            },
        }
        vulnerability = {
            "sample_idx": int(sample_idx),
            "year": decoded["year"],
            "exp_ssp": self.exp_ssp_options[int(raw_row["EXP_SSP_IDX"])] if self.exp_ssp_options else None,
            **{
                key: float(raw_row[key])
                for key in (
                    "VULN_K",
                    "VULN_PHI_2050",
                    "VULN_DRMKC_SCALE_FB",
                    "VULN_DRMKC_SCALE_UE",
                    "VULN_GVI_SCALE_FB",
                    "VULN_GVI_SCALE_UE",
                    "VULN_RETROFIT_RATE",
                    "VULN_GROWTH_SENS",
                    "VULN_GROWTH_CAP",
                    "VULN_NEW_BUILD",
                )
            },
            **{key: value for key, value in out.items() if str(key).startswith("vuln_")},
        }
        return {
            "sample": sample_row,
            "impact": impact,
            "cba_ews": cba_ews,
            "cba_ac": cba_ac,
            "cba_trees": cba_trees,
            "vulnerability": vulnerability,
            "trajectories": self.build_policy_trajectory_rows(sample_idx, out),
            "qa": self.validate_sample_output(sample_idx, decoded, out),
        }

    def _load_or_evaluate_sample(
        self,
        sample_idx: int,
        raw_row: pd.Series,
    ) -> tuple[dict[str, Any], bool]:
        """Resume an exact sample checkpoint or atomically create one."""
        checkpoint_path = self.checkpoint_dir / f"sample_{sample_idx:05d}.json"
        raw_parameters = _json_ready(raw_row.to_dict())
        raw_hash = _sha256_json(raw_parameters)
        signature = self.run_provenance["campaign_signature"]
        if checkpoint_path.exists():
            payload = _load_json(checkpoint_path)
            if (
                payload.get("output_schema_version") != OUTPUT_SCHEMA_VERSION
                or payload.get("campaign_signature") != signature
                or payload.get("raw_parameters_sha256") != raw_hash
                or int(payload.get("sample_idx", -999999)) != int(sample_idx)
            ):
                raise RuntimeError(
                    f"[{self.slug}] checkpoint provenance mismatch at sample {sample_idx}; "
                    f"refusing to reuse {checkpoint_path}."
                )
            records_encoded = payload.get("records")
            if not isinstance(records_encoded, dict):
                raise RuntimeError(f"[{self.slug}] invalid checkpoint payload: {checkpoint_path}")
            if payload.get("records_sha256") != _sha256_json(records_encoded):
                raise RuntimeError(f"[{self.slug}] checkpoint integrity failure: {checkpoint_path}")
            records = _json_restore(records_encoded)
            qa = records.get("qa", [])
            if not qa or any(row.get("status") != "pass" for row in qa):
                raise RuntimeError(f"[{self.slug}] checkpoint contains failed or missing QA: {checkpoint_path}")
            if int(records.get("sample", {}).get("sample_idx", -999999)) != int(sample_idx):
                raise RuntimeError(f"[{self.slug}] checkpoint has the wrong sample record: {checkpoint_path}")
            trajectories = records.get("trajectories", [])
            if len(trajectories) != HORIZON_YEARS * len(BRANCH_NAMES):
                raise RuntimeError(f"[{self.slug}] checkpoint has incomplete trajectories: {checkpoint_path}")
            expected_years = range(int(min(self.years)), int(min(self.years)) + HORIZON_YEARS)
            expected_keys = {(int(year), branch) for year in expected_years for branch in BRANCH_NAMES}
            found_keys = {
                (int(row.get("year", -999999)), str(row.get("branch", "")))
                for row in trajectories
                if int(row.get("sample_idx", -999999)) == int(sample_idx)
            }
            if found_keys != expected_keys:
                raise RuntimeError(f"[{self.slug}] checkpoint has invalid trajectory keys: {checkpoint_path}")
            return records, True

        out = self.evaluate_sample(raw_row)
        records = self._records_for_sample(sample_idx, raw_row, out)
        records_encoded = _json_ready(records)
        payload = {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "campaign_signature": signature,
            "sample_idx": int(sample_idx),
            "raw_parameters": raw_parameters,
            "raw_parameters_sha256": raw_hash,
            "records": records_encoded,
            "records_sha256": _sha256_json(records_encoded),
        }
        _atomic_write_json(checkpoint_path, payload)
        return records, False

    def run(self, n: int, seed: int = SEED_DEFAULT, make_figures: bool = False) -> dict[str, Path]:
        if int(n) <= 0:
            raise ValueError("N must be a positive integer.")
        raw_samples, x = self.sample_parameters(int(n), int(seed))
        self.prepare_campaign(raw_samples, n=int(n), seed=int(seed))

        # The canonical point is an integration/parity control only. It is
        # deliberately excluded from the LHS, uncertainty ranges and PAWN.
        central_paths = self.run_central_control()
        print(f"[{self.slug}] central NB01--NB08 parity control passed; starting N={n} LHS")

        records_by_kind: dict[str, list[Any]] = {
            "sample": [],
            "impact": [],
            "cba_ews": [],
            "cba_ac": [],
            "cba_trees": [],
            "vulnerability": [],
            "trajectories": [],
            "qa": [],
        }

        for idx, raw_row in raw_samples.iterrows():
            records, resumed = self._load_or_evaluate_sample(int(idx), raw_row)
            for kind in ("sample", "impact", "cba_ews", "cba_ac", "cba_trees", "vulnerability"):
                records_by_kind[kind].append(records[kind])
            records_by_kind["trajectories"].extend(records["trajectories"])
            records_by_kind["qa"].extend(records["qa"])
            sample_record = records["sample"]
            action = "resumed" if resumed else "evaluated"
            print(
                f"[{self.slug}] sample {idx + 1}/{n} {action}: "
                f"year={sample_record['year']} aai={float(sample_record['aai_agg']):.3f}"
            )

        samples_df = pd.DataFrame(records_by_kind["sample"]).sort_values("sample_idx").reset_index(drop=True)
        impact_df = pd.DataFrame(records_by_kind["impact"]).sort_values("sample_idx").reset_index(drop=True)
        cba_ews_df = pd.DataFrame(records_by_kind["cba_ews"]).sort_values("sample_idx").reset_index(drop=True)
        cba_ac_df = pd.DataFrame(records_by_kind["cba_ac"]).sort_values("sample_idx").reset_index(drop=True)
        cba_tree_df = pd.DataFrame(records_by_kind["cba_trees"]).sort_values("sample_idx").reset_index(drop=True)
        vuln_df = pd.DataFrame(records_by_kind["vulnerability"]).sort_values("sample_idx").reset_index(drop=True)
        trajectories_df = pd.DataFrame(records_by_kind["trajectories"]).sort_values(
            ["sample_idx", "year", "branch"]
        ).reset_index(drop=True)
        sample_qa_df = pd.DataFrame(records_by_kind["qa"]).sort_values(
            ["sample_idx", "metric"]
        ).reset_index(drop=True)
        self.validate_uq_sample_outputs(samples_df, trajectories_df, sample_qa_df)

        sens_aai_df = _pawn_table(self.problem, x, {"aai_agg": samples_df["aai_agg"].to_numpy(float)})
        sens_freq_df = _pawn_table(
            self.problem,
            x,
            {f"daily_p{p}": samples_df[f"daily_p{p}"].to_numpy(float) for p in DAILY_QUANTILE_PCTS},
        )
        sens_cba_df = _pawn_table(
            self.problem,
            x,
            {
                "ews_pv_cost_25y": samples_df["ews_pv_cost_25y"].to_numpy(float),
                "ews_net_avoided_deaths_25y_cum": samples_df["ews_net_avoided_deaths_25y_cum"].to_numpy(float),
                "ews_cost_per_net_death_25y_cum": samples_df["ews_cost_per_net_death_25y_cum"].to_numpy(float),
            },
            log_metrics={"ews_cost_per_net_death_25y_cum"},
        )
        sens_cba_ac_df = _pawn_table(
            self.problem,
            x,
            {
                "ac_pv_cost_25y": samples_df["ac_pv_cost_25y"].to_numpy(float),
                "ac_gross_avoided_deaths_25y_cum": samples_df["ac_gross_avoided_deaths_25y_cum"].to_numpy(float),
                "ac_net_avoided_deaths_25y_cum": samples_df["ac_net_avoided_deaths_25y_cum"].to_numpy(float),
                "ac_cost_per_net_death_25y_cum": samples_df["ac_cost_per_net_death_25y_cum"].to_numpy(float),
            },
            log_metrics={"ac_cost_per_net_death_25y_cum"},
        )
        sens_cba_tree_df = _pawn_table(
            self.problem,
            x,
            {
                "tree_pv_cost_25y": samples_df["tree_pv_cost_25y"].to_numpy(float),
                "tree_avoided_deaths_25y_cum": samples_df["tree_avoided_deaths_25y_cum"].to_numpy(float),
                "tree_cost_per_death_25y_cum": samples_df["tree_cost_per_death_25y_cum"].to_numpy(float),
            },
            log_metrics={"tree_cost_per_death_25y_cum"},
        )

        # Vulnerability PAWN sensitivity
        vuln_pawn_outputs: dict[str, np.ndarray] = {}
        for col in [
            "vuln_svi_mean", "vuln_svi_p90_p10_gap", "vuln_pop_weighted_svi",
            "vuln_2050_svi_mean", "vuln_2050_svi_p90_p10_gap", "vuln_2050_pop_weighted_svi",
        ]:
            if col in samples_df.columns:
                vals = samples_df[col].to_numpy(float)
                if np.any(np.isfinite(vals)):
                    vuln_pawn_outputs[col] = vals
        sens_vuln_df = _pawn_table(self.problem, x, vuln_pawn_outputs) if vuln_pawn_outputs else pd.DataFrame()

        paths = self.save_outputs(
            samples_df,
            impact_df,
            cba_ews_df,
            cba_ac_df,
            cba_tree_df,
            vuln_df,
            sens_aai_df,
            sens_freq_df,
            sens_cba_df,
            sens_cba_ac_df,
            sens_cba_tree_df,
            sens_vuln_df,
            trajectories_df,
            sample_qa_df,
        )
        paths.update({key: value for key, value in central_paths.items() if value is not None})
        if make_figures:
            self.make_figures(samples_df, sens_aai_df, sens_cba_df, sens_vuln_df)
        self.write_completion_marker(paths, n_samples=len(samples_df))
        return paths

    def save_outputs(
        self,
        samples_df: pd.DataFrame,
        impact_df: pd.DataFrame,
        cba_ews_df: pd.DataFrame,
        cba_ac_df: pd.DataFrame,
        cba_tree_df: pd.DataFrame,
        vuln_df: pd.DataFrame,
        sens_aai_df: pd.DataFrame,
        sens_freq_df: pd.DataFrame,
        sens_cba_df: pd.DataFrame,
        sens_cba_ac_df: pd.DataFrame,
        sens_cba_tree_df: pd.DataFrame,
        sens_vuln_df: pd.DataFrame,
        trajectories_df: pd.DataFrame,
        sample_qa_df: pd.DataFrame,
    ) -> dict[str, Path]:
        if self.ews_uses_event_mask_warning():
            ews_note = (
                "EWS warning days follow the extreme event-day mask (Track-B); deaths-threshold quantile calibration "
                "is bypassed in NB09 for this city-track setup."
            )
            recalib_note = (
                "Threshold recalibration options are kept for compatibility but are not applied when event-mask "
                "warning trigger mode is active."
            )
            trigger_note = (
                "Track-B warning-trigger uncertainty is sampled via event-definition controls "
                "(threshold percentile and minimum event duration)."
            )
        else:
            ews_note = "EWS is applied with event-level warning-day logic calibrated on the sampled reference-year mortality distribution."
            recalib_note = "Threshold recalibration is represented as stepwise population-scaled updates at the sampled interval."
            trigger_note = "Track-B event-definition trigger dimensions are inactive in standard deaths-threshold mode."

        paths = {
            "samples": self.unc_dir / f"unc_samples_{self.slug}_improved_fast.csv",
            "impact": self.unc_dir / f"unc_impact_summary_{self.slug}_improved_fast.csv",
            "freq": self.unc_dir / f"unc_freq_curve_{self.slug}_improved_fast.csv",
            "cba": self.unc_dir / f"unc_cba_ews_{self.slug}_improved_fast.csv",
            "cba_ac": self.unc_dir / f"unc_cba_ac_{self.slug}_improved_fast.csv",
            "cba_trees": self.unc_dir / f"unc_cba_trees_{self.slug}_improved_fast.csv",
            "vuln": self.unc_dir / f"unc_vulnerability_{self.slug}_improved_fast.csv",
            "trajectories": self.unc_dir / f"unc_policy_trajectories_25y_{self.slug}_improved_fast.csv",
            "sample_qa": self.unc_dir / f"sample_mathematical_qa_{self.slug}_improved_fast.csv",
            "aggregate_qa": self.unc_dir / f"uq_output_qa_{self.slug}_improved_fast.csv",
            "sens_aai": self.unc_dir / f"sens_aai_agg_{self.slug}_improved_fast.csv",
            "sens_freq": self.unc_dir / f"sens_freq_curve_{self.slug}_improved_fast.csv",
            "sens_cba": self.unc_dir / f"sens_cba_ews_{self.slug}_improved_fast.csv",
            "sens_cba_ac": self.unc_dir / f"sens_cba_ac_{self.slug}_improved_fast.csv",
            "sens_cba_trees": self.unc_dir / f"sens_cba_trees_{self.slug}_improved_fast.csv",
            "sens_vuln": self.unc_dir / f"sens_vulnerability_{self.slug}_improved_fast.csv",
            "meta": self.unc_dir / f"uq_dimensions_{self.slug}_improved_fast.json",
            "bundle": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved_fast.h5",
            "bundle_sens": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved_fast_with_sensitivity.h5",
            "lhs_design": self.lhs_design_path,
            "run_manifest": self.run_manifest_path,
            "completion": self.unc_dir / "CAMPAIGN_COMPLETE.json",
        }
        _atomic_write_csv(paths["samples"], samples_df, index=False)
        _atomic_write_csv(paths["impact"], impact_df, index=False)
        _atomic_write_csv(
            paths["freq"],
            impact_df[["sample_idx", *[f"daily_p{p}" for p in DAILY_QUANTILE_PCTS]]],
            index=False,
        )
        _atomic_write_csv(paths["cba"], cba_ews_df, index=False)
        _atomic_write_csv(paths["cba_ac"], cba_ac_df, index=False)
        _atomic_write_csv(paths["cba_trees"], cba_tree_df, index=False)
        _atomic_write_csv(paths["vuln"], vuln_df, index=False)
        _atomic_write_csv(paths["trajectories"], trajectories_df, index=False)
        _atomic_write_csv(paths["sample_qa"], sample_qa_df, index=False)
        _atomic_write_csv(paths["sens_aai"], sens_aai_df, index=False)
        _atomic_write_csv(paths["sens_freq"], sens_freq_df, index=False)
        _atomic_write_csv(paths["sens_cba"], sens_cba_df, index=False)
        _atomic_write_csv(paths["sens_cba_ac"], sens_cba_ac_df, index=False)
        _atomic_write_csv(paths["sens_cba_trees"], sens_cba_tree_df, index=False)
        if not sens_vuln_df.empty:
            _atomic_write_csv(paths["sens_vuln"], sens_vuln_df, index=False)

        meta = {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "run_provenance": self.run_provenance,
            "n_samples": int(self.run_provenance["n_samples"]),
            "seed": int(self.run_provenance["seed"]),
            "git_commit": self.run_provenance.get("git", {}).get("commit"),
            "city": self.city,
            "slug": self.slug,
            "hazard_track": self.hazard_track,
            "hazard_events_csv": str(self.haz_events_csv),
            "extreme_threshold_degC": float(self.extreme_tstar_c) if np.isfinite(self.extreme_tstar_c) else None,
            "extreme_min_duration_days": int(self.extreme_min_duration_days) if self.use_extreme_track else None,
            "extreme_season_start_md": self.extreme_season_start_md if self.use_extreme_track else None,
            "extreme_season_end_md": self.extreme_season_end_md if self.use_extreme_track else None,
            "years": self.years,
            "baseline_modes": self.baseline_modes,
            "climate_scenarios": self.clim_scens,
            "climate_bands": self.clim_bands,
            "climate_available_bands_by_scenario": self.climate_available_bands_by_scenario,
            "climate_forced_central_scenarios": self.climate_forced_central_scenarios,
            "climate_source_options": self.clim_source_options,
            "gcm_options": self.gcm_options,
            "if_families": self.available_if_families,
            "t_ref_options": TREF_OPTIONS,
            "ac_ssp_options": self.ac_ssp_options,
            "exp_ssp_options": self.exp_ssp_options,
            "ac_efficacy_scenarios": self.efficacy_scenarios,
            "ews_interp_options": self.ews_interp_options,
            "ews_level_options": self.level_options,
            "ews_warning_trigger_mode": self.ews_warning_trigger_mode,
            "ews_nonpositive_threshold_fallback": self.ews_nonpositive_fallback,
            "ews_threshold_meta_path": self.threshold_meta_path,
            "ews_target_days_options": self.ews_target_days_options,
            "ews_recalib_options": self.ews_recalib_options,
            "extreme_threshold_percentile_options": self.extreme_threshold_options,
            "extreme_min_duration_options": self.extreme_min_duration_options,
            "wh_enabled_options": self.wh_enabled_options,
            "cop_enabled_options": self.cop_enabled_options,
            "tree_ramp_options": self.tree_ramp_options,
            "tree_start_age_options": self.tree_start_age_options,
            "economic_options": {
                "discount_rate": [0.02, 0.03, 0.05],
                "ac_capex_mult": [0.8, 1.0, 1.2],
                "ac_capex_per_user_base": self.ac_capex_base,
                "ac_tariff_eur_per_kwh": self.ac_tariff_options,
                "ac_lifetime_years": [9, 12, 16],
                "tree_capex_mult": [0.8, 1.0, 1.2],
                "tree_om_mult": [1.0, 5.0],
            },
            "policy_branches": {
                "reference": "Mortality with autonomous/current AC and no additional policy.",
                "ac_policy_gross": "AC-policy mortality before the outdoor waste-heat penalty.",
                "ac_policy_net": "AC-policy mortality after its incremental outdoor waste-heat penalty.",
                "ac_policy_net_with_tree_feedback": (
                    "NB08-compatible AC-with-trees interaction: the gross AC mortality benefit is unchanged, "
                    "while trees reduce AC electricity use and the AC waste-heat penalty; direct tree mortality "
                    "benefits and tree costs are excluded."
                ),
                "tree_policy": "Standalone dynamic tree-policy mortality relative to the reference branch.",
                "ews_policy": "Standalone EWS mortality relative to the same reference branch, including AC overlap.",
                "ac_tree_policy_gross": (
                    "Full combined AC-plus-tree mortality before the tree-adjusted AC waste-heat penalty."
                ),
                "ac_tree_policy_net": (
                    "Full combined AC-plus-tree mortality after the tree-adjusted AC waste-heat penalty."
                ),
            },
            "trajectory_export": {
                "years": [int(min(self.years)), int(min(self.years)) + HORIZON_YEARS - 1],
                "rows_per_sample": HORIZON_YEARS * len(BRANCH_NAMES),
                "avoided_deaths_definition": "reference annual deaths minus policy-branch annual deaths within the same sample and year",
                "annual_cost_eur": "undiscounted end-of-policy-year cash flow, except EWS setup CAPEX at policy-year t=0 as in NB06/NB08",
                "pv_cost_eur": "the corresponding annual contribution to present value",
            },
            "vuln_param_ranges": {
                "VULN_K": [0.55, 0.95],
                "VULN_PHI_2050": [0.50, 0.90],
                "VULN_DRMKC_SCALE_FB": [0.02, 0.08],
                "VULN_DRMKC_SCALE_UE": [0.04, 0.16],
                "VULN_GVI_SCALE_FB": [0.15, 0.55],
                "VULN_GVI_SCALE_UE": [0.25, 0.75],
                "VULN_RETROFIT_RATE": [0.005, 0.020],
                "VULN_GROWTH_SENS": [0.50, 1.00],
                "VULN_GROWTH_CAP": [0.25, 0.45],
                "VULN_NEW_BUILD": [0.10, 0.25],
            },
            "notes": [
                "Future T2M bands are sampled from across-GCM band tables, while tas uses avg(pct45,pct55) within each model upstream.",
                "AC protection is applied spatially to each exposure cell as 1 - efficacy_age * coverage_cell.",
                "The vegetation dLST-to-dT2M bridge is evaluated day by day at each cell's actual daily T2M, matching NB07.",
                "Waste heat follows NB05/NB08 aggregate accounting: the reported policy penalty is policy-AC feedback minus current-AC feedback; it is not injected into the spatial hazard.",
                "aai_agg and annual_deaths denote standalone reference annual heat deaths for the sampled year; explicit policy-branch annual and 25-year outputs are exported separately.",
                ews_note,
                recalib_note,
                trigger_note,
                "When a sampled climate band is unavailable for a sampled scenario, the effective band is forced to central (e.g., ssp585 when only central is provided in the city delta table).",
                "CBA uncertainty now samples discounting plus AC/tree cost parameters in the same global sample as the impact-chain uncertainty.",
                "AC CBA is evaluated as policy AC versus current/autonomous AC under the same sampled hazard, exposure, IF and vulnerability settings.",
                "Tree CBA is evaluated as tree policy versus the same sampled no-tree reference branch.",
                "Each LHS row is a complete plausible model configuration in an exploratory multi-dimensional uncertainty ensemble; reported ranges are not confidence intervals.",
                "Every policy outcome is paired with the reference branch from the same LHS row. The central configuration is evaluated separately, outside the LHS and PAWN, solely as an NB01--NB08 integration/parity control.",
                "PAWN indices are screening-oriented marginal distribution-based sensitivity diagnostics.",
                "Vulnerability projection uncertainty (Level A): 10 parameters perturbed, SVI recomputed on-the-fly; output-only, does not affect mortality.",
                f"All production artifacts are isolated under {self.unc_dir}.",
            ],
        }
        _atomic_write_json(paths["meta"], meta)

        bundle_tmp = paths["bundle"].with_name(f".{paths['bundle'].name}.tmp.{os.getpid()}")
        with pd.HDFStore(str(bundle_tmp), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_ews_df
            store["cba_ac"] = cba_ac_df
            store["cba_trees"] = cba_tree_df
            store["vulnerability"] = vuln_df
            store["trajectories"] = trajectories_df
            store["sample_qa"] = sample_qa_df
        os.replace(bundle_tmp, paths["bundle"])
        bundle_sens_tmp = paths["bundle_sens"].with_name(
            f".{paths['bundle_sens'].name}.tmp.{os.getpid()}"
        )
        with pd.HDFStore(str(bundle_sens_tmp), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_ews_df
            store["cba_ac"] = cba_ac_df
            store["cba_trees"] = cba_tree_df
            store["vulnerability"] = vuln_df
            store["trajectories"] = trajectories_df
            store["sample_qa"] = sample_qa_df
            store["sens_aai"] = sens_aai_df
            store["sens_freq"] = sens_freq_df
            store["sens_cba"] = sens_cba_df
            store["sens_cba_ac"] = sens_cba_ac_df
            store["sens_cba_trees"] = sens_cba_tree_df
            if not sens_vuln_df.empty:
                store["sens_vuln"] = sens_vuln_df
        os.replace(bundle_sens_tmp, paths["bundle_sens"])
        return paths

    def write_completion_marker(self, paths: dict[str, Path], *, n_samples: int | None = None) -> Path:
        """Write the final marker only after all campaign outputs are durable."""
        completion = paths.get("completion", self.unc_dir / "CAMPAIGN_COMPLETE.json")
        hash_keys = (
            "samples",
            "impact",
            "cba",
            "cba_ac",
            "cba_trees",
            "vuln",
            "trajectories",
            "sample_qa",
            "aggregate_qa",
            "meta",
            "lhs_design",
            "run_manifest",
            "central",
            "central_mathematical_qa",
            "central_parity",
            "central_control_completion",
            "freq",
            "sens_aai",
            "sens_freq",
            "sens_cba",
            "sens_cba_ac",
            "sens_cba_trees",
            "sens_vuln",
            "bundle",
            "bundle_sens",
        )
        output_hashes = {
            key: _sha256_file(paths[key])
            for key in hash_keys
            if key in paths and paths[key].is_file()
        }
        if n_samples is None:
            samples_path = paths.get("samples")
            n_samples = len(pd.read_csv(samples_path)) if samples_path is not None and samples_path.exists() else None
        marker = {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "campaign_id": self.run_provenance["campaign_id"],
            "campaign_signature": self.run_provenance["campaign_signature"],
            "city": self.city,
            "slug": self.slug,
            "n_samples": int(n_samples) if n_samples is not None else None,
            "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "output_sha256": output_hashes,
        }
        _atomic_write_json(completion, marker)
        return completion

    def make_figures(self, samples_df: pd.DataFrame, sens_aai_df: pd.DataFrame, sens_cba_df: pd.DataFrame, sens_vuln_df: pd.DataFrame | None = None) -> None:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4))
        aai = samples_df["aai_agg"].to_numpy(float)
        ax.hist(aai, bins=40, alpha=0.7, color="#3A7CA5", edgecolor="white")
        ax.axvline(np.percentile(aai, 5), color="crimson", ls=":", lw=1.2)
        ax.axvline(np.percentile(aai, 50), color="crimson", ls="--", lw=1.2)
        ax.axvline(np.percentile(aai, 95), color="crimson", ls=":", lw=1.2)
        ax.set_title(f"{self.city} - aai_agg distribution (improved)")
        ax.set_xlabel("aai_agg")
        ax.set_ylabel("Count")
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_distribution_aai_agg_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        x, y = _ecdf(aai[np.isfinite(aai)])
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, color="#3A7CA5", lw=1.8)
        ax.set_title(f"{self.city} - aai_agg empirical CDF (improved)")
        ax.set_xlabel("aai_agg")
        ax.set_ylabel("ECDF")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_cdf_aai_agg_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        mean_df = sens_aai_df[sens_aai_df["si"].astype(str).str.lower() == "mean"].copy()
        if mean_df.empty:
            mean_df = sens_aai_df.copy()
        top = mean_df.sort_values("aai_agg", ascending=False).head(12)
        fig, ax = plt.subplots(figsize=(8, 5))
        plot_df = top.iloc[::-1]
        ax.barh(plot_df["param"].astype(str), plot_df["aai_agg"].astype(float), color="#4C78A8")
        ax.set_xlabel("PAWN sensitivity (mean)")
        ax.set_title(f"{self.city} - Tornado (aai_agg, improved)")
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"sens_tornado_aai_agg_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8, 4))
        cpd = samples_df["ews_cost_per_net_death_25y_cum"].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
        if cpd.size:
            ax.hist(cpd, bins=40, alpha=0.7, color="#8B5FBF", edgecolor="white")
        ax.set_title(f"{self.city} - EWS cost per net death (25y, improved)")
        ax.set_xlabel("EUR / net avoided death")
        ax.set_ylabel("Count")
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_distribution_cba_ews_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        x, y = _ecdf(cpd)
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, color="#8B5FBF", lw=1.8)
        ax.set_title(f"{self.city} - EWS cost per net death empirical CDF (improved)")
        ax.set_xlabel("EUR / net avoided death")
        ax.set_ylabel("ECDF")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_cdf_cba_ews_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        mean_cba = sens_cba_df[sens_cba_df["si"].astype(str).str.lower() == "mean"].copy()
        if not mean_cba.empty and "ews_cost_per_net_death_25y_cum" in mean_cba.columns:
            top_cba = mean_cba.sort_values("ews_cost_per_net_death_25y_cum", ascending=False).head(12)
            fig, ax = plt.subplots(figsize=(8, 5))
            plot_df = top_cba.iloc[::-1]
            ax.barh(plot_df["param"].astype(str), plot_df["ews_cost_per_net_death_25y_cum"].astype(float), color="#C76D2D")
            ax.set_xlabel("PAWN sensitivity (mean)")
            ax.set_title(f"{self.city} - Tornado (EWS €/death, improved)")
            ax.grid(axis="x", alpha=0.3)
            plt.tight_layout()
            plt.savefig(self.unc_dir / f"sens_tornado_cba_ews_{self.slug}_improved_fast.png", dpi=160)
            plt.close(fig)

        freq_cols = [f"daily_p{p}" for p in DAILY_QUANTILE_PCTS]
        freq_df = samples_df[freq_cols].replace([np.inf, -np.inf], np.nan)
        q05 = freq_df.quantile(0.05)
        q50 = freq_df.quantile(0.50)
        q95 = freq_df.quantile(0.95)
        x_pct = np.asarray(DAILY_QUANTILE_PCTS, dtype=float)
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        ax.fill_between(x_pct, q05.to_numpy(float), q95.to_numpy(float), color="#4C78A8", alpha=0.22, label="5-95%")
        ax.plot(x_pct, q50.to_numpy(float), marker="o", color="#1F4E79", lw=1.8, label="median")
        ax.set_title(f"{self.city} - daily heat-death quantiles (improved)")
        ax.set_xlabel("Daily-death percentile")
        ax.set_ylabel("Deaths on that day")
        ax.set_xticks(x_pct)
        ax.grid(alpha=0.3)
        ax.legend(frameon=False)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_freq_curve_{self.slug}_improved_fast.png", dpi=160)
        plt.close(fig)

        # Vulnerability figures 
        if "vuln_svi_mean" in samples_df.columns:
            svi_vals = samples_df["vuln_svi_mean"].dropna().to_numpy(float)
            if svi_vals.size > 0:
                fig, ax = plt.subplots(figsize=(8, 4))
                ax.hist(svi_vals, bins=40, alpha=0.7, color="#2D6A4F", edgecolor="white")
                ax.axvline(np.percentile(svi_vals, 5), color="crimson", ls=":", lw=1.2)
                ax.axvline(np.percentile(svi_vals, 50), color="crimson", ls="--", lw=1.2)
                ax.axvline(np.percentile(svi_vals, 95), color="crimson", ls=":", lw=1.2)
                ax.set_title(f"{self.city} - SVI mean distribution (improved)")
                ax.set_xlabel("SVI mean")
                ax.set_ylabel("Count")
                plt.tight_layout()
                plt.savefig(self.unc_dir / f"unc_distribution_vuln_svi_mean_{self.slug}_improved_fast.png", dpi=160)
                plt.close(fig)

        if "vuln_2050_svi_mean" in samples_df.columns:
            svi_2050 = samples_df["vuln_2050_svi_mean"].dropna().to_numpy(float)
            if svi_2050.size > 0:
                fig, ax = plt.subplots(figsize=(8, 4))
                ax.hist(svi_2050, bins=40, alpha=0.7, color="#40916C", edgecolor="white")
                ax.axvline(np.percentile(svi_2050, 5), color="crimson", ls=":", lw=1.2)
                ax.axvline(np.percentile(svi_2050, 50), color="crimson", ls="--", lw=1.2)
                ax.axvline(np.percentile(svi_2050, 95), color="crimson", ls=":", lw=1.2)
                ax.set_title(f"{self.city} - SVI mean 2050 distribution (improved)")
                ax.set_xlabel("SVI mean (2050)")
                ax.set_ylabel("Count")
                plt.tight_layout()
                plt.savefig(self.unc_dir / f"unc_distribution_vuln_2050_svi_mean_{self.slug}_improved_fast.png", dpi=160)
                plt.close(fig)

        if sens_vuln_df is not None and not sens_vuln_df.empty:
            gap_col = "vuln_2050_svi_p90_p10_gap"
            gap_summary = _summarize_sensitivity(
                sens_vuln_df,
                gap_col,
                si="mean",
                exclude_params={"YEAR_IDX"},
            )
            if not gap_summary.empty:
                fig, ax = plt.subplots(figsize=(8, 5))
                plot_df = gap_summary.head(12).iloc[::-1]
                ax.barh(plot_df["param"].astype(str), plot_df[gap_col].astype(float), color="#2D6A4F")
                ax.set_xlabel("PAWN sensitivity (mean)")
                ax.set_title(f"{self.city} - Tornado (SVI P90-P10 gap 2050, improved)")
                ax.grid(axis="x", alpha=0.3)
                plt.tight_layout()
                plt.savefig(self.unc_dir / f"sens_tornado_vuln_svi_gap_{self.slug}_improved_fast.png", dpi=160)
                plt.close(fig)

            mean_col = "vuln_2050_svi_mean"
            mean_summary = _summarize_sensitivity(
                sens_vuln_df,
                mean_col,
                si="mean",
                exclude_params={"YEAR_IDX"},
            )
            if not mean_summary.empty:
                fig, ax = plt.subplots(figsize=(8, 5))
                plot_df = mean_summary.head(12).iloc[::-1]
                ax.barh(plot_df["param"].astype(str), plot_df[mean_col].astype(float), color="#40916C")
                ax.set_xlabel("PAWN sensitivity (mean)")
                ax.set_title(f"{self.city} - Tornado (SVI mean 2050, improved)")
                ax.grid(axis="x", alpha=0.3)
                plt.tight_layout()
                plt.savefig(self.unc_dir / f"sens_tornado_vuln_svi_mean_{self.slug}_improved_fast.png", dpi=160)
                plt.close(fig)


def regenerate_saved_figures(city: str) -> Path:
    slug = city.strip().lower()
    runner = NB09ImprovedFast(slug)
    samples_df = pd.read_csv(runner.unc_dir / f"unc_samples_{runner.slug}_improved_fast.csv")
    sens_aai_df = pd.read_csv(runner.unc_dir / f"sens_aai_agg_{runner.slug}_improved_fast.csv")
    sens_cba_df = pd.read_csv(runner.unc_dir / f"sens_cba_ews_{runner.slug}_improved_fast.csv")
    sens_vuln_path = runner.unc_dir / f"sens_vulnerability_{runner.slug}_improved_fast.csv"
    sens_vuln_df = pd.read_csv(sens_vuln_path) if sens_vuln_path.exists() else pd.DataFrame()
    runner.make_figures(samples_df, sens_aai_df, sens_cba_df, sens_vuln_df)
    return runner.unc_dir


def run_nb09_improved_fast(
    city: str | None = None,
    n: int | None = None,
    seed: int | None = None,
    make_figures: bool | None = None,
) -> dict[str, Path]:
    slug = (city or os.environ.get("CITY") or "rome").strip().lower()
    n_use = int(n if n is not None else os.environ.get("NB09_N", 128))
    seed_use = int(seed if seed is not None else os.environ.get("NB09_SEED", SEED_DEFAULT))
    make_figures_env = os.environ.get("NB09_MAKE_FIGURES", "0").strip().lower() in {"1", "true", "yes", "y"}
    make_figures_use = make_figures_env if make_figures is None else bool(make_figures)
    runner = NB09ImprovedFast(slug)
    return runner.run(n=n_use, seed=seed_use, make_figures=make_figures_use)


def main() -> None:
    parser = argparse.ArgumentParser(description="Improved-fast March2026 NB09 uncertainty workflow.")
    parser.add_argument("--city", default=os.environ.get("CITY", "rome"), help="Configured city slug")
    parser.add_argument("--n", type=int, default=int(os.environ.get("NB09_N", 128)), help="Latin hypercube sample size")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("NB09_SEED", SEED_DEFAULT)), help="Sampling seed")
    parser.add_argument("--figures-only", action="store_true", help="Regenerate saved figures from existing improved-fast outputs")
    parser.add_argument(
        "--make-figures",
        action="store_true",
        help="Generate figures during the main uncertainty run (off by default for speed).",
    )
    args = parser.parse_args()
    if args.figures_only:
        out_dir = regenerate_saved_figures(args.city)
        print(f"Regenerated improved figures in: {out_dir}")
    else:
        paths = run_nb09_improved_fast(city=args.city, n=args.n, seed=args.seed, make_figures=args.make_figures)
        print("Saved improved-fast uncertainty outputs in:")
        for key, path in paths.items():
            print(f"  {key}: {path}")


if __name__ == "__main__":
    main()

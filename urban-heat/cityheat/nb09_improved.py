# this is the main uncertainty quantification script

from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio as rio
import xarray as xr
import yaml
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
from scipy import sparse
from scipy.stats import qmc


AGE_ORDER = ["<15", "15-64", "65+"]
AGE_TO_ID = {"<15": 1, "15-64": 2, "65+": 3}
IF_FAMILIES = ["burke_polynomial", "burke_powerlaw"]
TREF_OPTIONS = [18.0, 20.0, 22.0, 24.0, 26.0]
TREF_BASE = 20.0
RETURN_PERIODS = [2, 5, 10, 20]
HORIZON_YEARS = 25
DISCOUNT_RATE_DEFAULT = 0.03
SEED_DEFAULT = 42


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
    rows: list[dict[str, Any]] = []
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
        for idx, param in enumerate(res["names"]):
            for si in ["minimum", "mean", "median", "maximum", "CV"]:
                rows.append({"si": si, "param": param, metric: float(res[si][idx])})

    if not rows or not analyzed_metrics:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    out = None
    for metric in analyzed_metrics:
        sub = df[["si", "param", metric]].copy()
        out = sub if out is None else out.merge(sub, on=["si", "param"], how="outer")
    out["param2"] = np.nan
    cols = ["si", "param", "param2", *analyzed_metrics]
    return out[cols]


def _freq_curve_from_daily(daily_impacts: np.ndarray, rps: list[int]) -> dict[str, float]:
    vals = np.asarray(daily_impacts, dtype=float)
    out: dict[str, float] = {}
    for rp in rps:
        q = float(np.clip(1.0 - 1.0 / float(rp), 0.0, 1.0))
        out[f"rp{int(rp)}"] = float(np.quantile(vals, q)) if vals.size else np.nan
    return out


def _safe_ratio(num: float, den: float) -> float:
    return float(num) / float(den) if float(den) > 0 else np.inf


def _pv_capex_with_replacements(
    new_users_t: np.ndarray,
    capex_per_user: float,
    lifetime_years: int,
    discount_rate: float,
) -> float:
    """Present value of cohort-based AC CAPEX with replacement cycles."""
    new_users_t = np.asarray(new_users_t, dtype=float)
    horizon = int(new_users_t.size)
    life = max(int(lifetime_years), 1)
    r = float(discount_rate)
    pv = 0.0
    for start_idx, cohort in enumerate(new_users_t):
        cohort = float(cohort)
        if cohort <= 0:
            continue
        pay_idx = int(start_idx)
        while pay_idx < horizon:
            pv += cohort * float(capex_per_user) / ((1.0 + r) ** float(pay_idx))
            pay_idx += life
    return float(pv)


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
    years = int(years)
    if years <= 0:
        return 0.0
    inc = float(delta_index_total) / float(years)
    r = float(discount_rate)
    pv = 0.0
    for t_idx in range(years):
        pv += float(capex_per_index_pt) * inc / ((1.0 + r) ** float(t_idx))
    return float(pv)


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
    discount = (1.0 + float(discount_rate)) ** np.arange(int(years), dtype=float)
    pv = float(np.sum(om_stream / discount))
    return pv, om_stream


@dataclass
class ParamSpec:
    name: str
    kind: str
    options: list[Any] | None = None
    low: float | None = None
    high: float | None = None


class NB09Improved:
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
        self.unc_dir = self.tab_dir / "uncertainty_improved"
        self.unc_dir.mkdir(parents=True, exist_ok=True)

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
            self._assert_city_pattern(csr)
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
        ds = xr.open_dataset(path)
        vname = "T2M" if "T2M" in ds.data_vars else list(ds.data_vars)[0]
        arr = ds[vname].transpose("time", "y", "x").values.astype(np.float32)
        ds.close()
        return arr

    def _load_climate_inputs(self) -> None:
        t2m_var = self.clim_cfg.get("t2m_var", "tas")
        bands_table = self.base_dir / "T2MmeanDeltas/climate_change_provide_markups_bands.csv"
        legacy_table = self.base_dir / self.cfg.get("files", {}).get(
            "t2m_deltas_table", "T2MmeanDeltas/climate_change_provide_markups_avg.csv"
        )
        gcm_table = self.base_dir / "T2MmeanDeltas/climate_change_provide_markups_gcm.csv"

        if bands_table.exists():
            deltas_df = pd.read_csv(bands_table)
            if "pct_band" not in deltas_df.columns:
                deltas_df["pct_band"] = "central"
        elif legacy_table.exists():
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

        pen_file = self.P(ac_cfg.get("penetration_file", ""))
        if not pen_file.exists():
            raise FileNotFoundError(f"Missing AC penetration file: {pen_file}")
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
        self.coverage_pattern_by_mode_year: dict[str, dict[int, np.ndarray]] = {"base": {}, "policy": {}}
        self.coverage_mean_by_mode_year: dict[str, dict[int, float]] = {"base": {}, "policy": {}}

        def _store_coverage(mode: str, cube: np.ndarray) -> None:
            for idx, y in enumerate(cov_years):
                arr = cube[idx if cube.shape[0] > 1 else 0]
                row_vals = arr.ravel()[self.row_cols].astype(np.float32)
                masked = row_vals[self.row_is_city]
                mean_val = float(masked.mean()) if masked.size else 0.0
                if mean_val <= 0:
                    pattern = np.ones_like(row_vals, dtype=np.float32)
                else:
                    pattern = np.zeros_like(row_vals, dtype=np.float32)
                    pattern[self.row_is_city] = (masked / mean_val).astype(np.float32)
                self.coverage_pattern_by_mode_year[mode][int(y)] = pattern
                self.coverage_mean_by_mode_year[mode][int(y)] = mean_val

        _store_coverage("base", cov_base_3d)
        _store_coverage("policy", cov_policy_3d)
        self.coverage_pattern_by_year = self.coverage_pattern_by_mode_year["base"]

        self.ac_cost_params_path = self._find_first_existing(
            [self.int_dir / f"ac_cost_params_{self.slug}.json", self.int_dir / f"ac_costs_{self.slug}.json"]
        )
        self.ac_cost_params = _load_json(self.ac_cost_params_path)

        # AC electricity tariff uncertainty should be anchored to each city config.
        tariff_base = float(
            self.ac_cost_params.get(
                "tariff_eur_per_kwh",
                self.ac_cost_params.get(
                    "tariff_eur_kwh",
                    self.ac_cfg.get("tariff_eur_per_kwh", self.ac_cfg.get("tariff_eur_kwh", 0.25)),
                ),
            )
        )
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
        # EWS interpretation (maturity/efficacy regime) as a UQ axis.
        # Config-centred bracket, mirroring `ews_ramp_options` above: the per-city
        # configured interpretation is the centre and the two regime extremes
        # ("marginal", "counterfactual") are the fixed anchors, deduped in scale
        # order. So marginal-/counterfactual-configured cities keep the historical
        # {marginal, counterfactual} pair unchanged, while an "intermediate"
        # (meteo-HHWS) config additionally samples the midpoint case (see NB06).
        _interp_scale = ["marginal", "intermediate", "counterfactual"]
        self.ews_interp_base = str(self.ews_cfg.get("interpretation", "marginal")).lower()
        _interp_center = self.ews_interp_base if self.ews_interp_base in _interp_scale else "marginal"
        if bool(self.ews_cfg.get("uq_interp_full_range", False)):
            # Escape hatch: sample the full marginal->intermediate->counterfactual range for every city.
            self.ews_interp_options = list(_interp_scale)
        else:
            _interp_keep = {"marginal", _interp_center, "counterfactual"}
            self.ews_interp_options = [x for x in _interp_scale if x in _interp_keep]
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
            self.int_dir / f"ews_threshold_deaths_{self.slug}_climate_only.json",
            self.int_dir / f"ews_threshold_deaths_{self.slug}.json",
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
        veg = np.load(veg_path)
        if "dT2M_month_ref_uniform_approx" not in veg.files:
            raise KeyError(f"{veg_path} is missing dT2M_month_ref_uniform_approx")
        tree_full = veg["dT2M_month_ref_uniform_approx"].reshape(12, -1).astype(np.float32)
        self.tree_month_maps = tree_full[:, self.row_cols].astype(np.float32)
        self.tree_month_maps[:, ~self.row_is_city] = 0.0
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

        # Precompute city-mean monthly dT2M for lambda_y (tree-cooled AC utilization)
        # tree_month_maps: (12, n_row_cols) — dT2M at each centroid for each month
        self.tree_dT2M_citymean_monthly = np.array([
            float(self.tree_month_maps[m][self.row_is_city].mean()) for m in range(12)
        ], dtype=float)  # shape (12,), negative = cooling

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
            ParamSpec("IF_FAMILY_IDX", "choice", options=list(range(len(IF_FAMILIES)))),
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
            ParamSpec("AC_CAPEX_PER_USER_IDX", "choice", options=[350.0, 500.0, 650.0]),
            ParamSpec("AC_TARIFF_EUR_PER_KWH_IDX", "choice", options=self.ac_tariff_options),
            ParamSpec("AC_LIFETIME_YEARS_IDX", "choice", options=[8, 10, 12]),
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
        if str(mode).lower() != "policy":
            return base_mean
        delta_mean = self.interpolate_coverage_mean(year, "policy") - self.interpolate_coverage_mean(year, "base")
        return float(np.clip(base_mean + float(delta_mean), 0.0, 0.98))

    def coverage_for_sample(self, year: int, ac_ssp: int, mode: str = "base") -> np.ndarray:
        pattern = self.interpolate_coverage_pattern(year, mode=mode)
        target_mean = self.coverage_mean_for_mode(year, ac_ssp, mode=mode)
        return _scale_pattern_to_mean_masked(pattern, self.row_is_city, target_mean, upper=0.98)

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
                exp.assign_centroids(self.hazard_template, distance="euclidean", threshold=0, overwrite=True)
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
        block = self.load_if_block(family, year)
        pen = float(self.coverage_mean_for_mode(year, ac_ssp, mode=ac_mode))
        ac_eff_map = self.cfg["efficacy_scenarios"][ac_eff_scen]
        funcs: list[ImpactFunc] = []
        for age in AGE_ORDER:
            rec = block[age]
            intensity = np.asarray(rec["intensity"], dtype=float) + (float(tref_c) - TREF_BASE)
            if self.use_extreme_track and np.isfinite(self.extreme_tstar_c):
                # Track-B hazards are event-day exceedances above T*.
                intensity = intensity - float(self.extreme_tstar_c)
            mdd = np.asarray(rec["mdd"], dtype=float)
            paa = np.asarray(rec.get("paa", np.ones_like(mdd)), dtype=float)
            age_scale = {"<15": mdd_scale_lt15, "15-64": mdd_scale_15_64, "65+": mdd_scale_65p}[age]
            mdd = mdd * float(age_scale) * (1.0 - float(disp_frac))
            ac_eff_age = float(ac_eff_map.get(age, ac_eff_map.get("default", 0.30)))
            ac_residual = float(np.clip(1.0 - ac_eff_age * pen, 0.0, 1.0))
            mdd = np.clip(mdd * ac_residual, 0.0, 1.0)
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
        citymean_pre_wh = self.ref_citymean_by_year[year] + mode_adj + clim_adj
        wh_activity_share = self.waste_heat_activity_share(citymean_pre_wh)

        wh_mode_use = str(wh_mode or ac_mode)
        coverage = self.coverage_for_sample(year, ac_ssp, mode=wh_mode_use)
        pen = float(self.coverage_mean_for_mode(year, ac_ssp, mode=wh_mode_use))
        dT_night = self.dT_night_from_penetration(pen, wh_case) if wh_enabled else 0.0
        if wh_enabled and cop_enabled:
            cop_sens = float(self.cop_sens.get(cop_case, self.cop_sens.get("central", 0.065)))
            amp = self.cop_amplification_factor(dT_night, cop_sens)
        else:
            amp = 1.0
        wh_city_scalar = float(wh_activity_share) * float(wh_ratio) * float(dT_night) * float(amp) if wh_enabled else 0.0
        if wh_city_scalar != 0.0 and coverage.size:
            coverage_mean = float(coverage[self.row_is_city].mean())
            wh_pattern = coverage / max(coverage_mean, 1e-6)
        else:
            wh_pattern = np.zeros(len(self.row_cols), dtype=np.float32)

        maturity = self.tree_maturity_factor(year, tree_ramp_years, tree_start_age)
        cap_ratio = float(tree_cap_uplift) / max(self.tree_base_cap, 1e-6)
        tree_scale = float(tree_coeff_scale) * cap_ratio * float(maturity) if tree_enabled else 0.0

        for row in range(n_days):
            a, b = indptr[row], indptr[row + 1]
            data[a:b] += float(mode_adj[row] + clim_adj[row])
            if wh_city_scalar != 0.0:
                data[a:b] += (wh_city_scalar * wh_pattern).astype(np.float32)
            if tree_scale != 0.0:
                data[a:b] += (self.tree_month_maps[int(months[row]) - 1] * tree_scale).astype(np.float32)

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
        return float(np.clip(self.ews_init + (1.0 - self.ews_init) * (dt_years / float(ews_ramp_years)), 0.0, 1.0))

    def pop_total_for_exposure(self, path: Path, scale: float) -> float:
        exp = self.load_exposure_cached(path)
        return float(exp.gdf["value"].astype(float).sum() * float(scale))

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

        age_impacts: dict[str, np.ndarray] = {}
        daily_total = np.zeros(len(self.dates_by_year[year]), dtype=float)
        for age in AGE_ORDER:
            exp_age = copy.deepcopy(self.load_exposure_age(exp_path, age))
            exp_age.gdf["value"] = exp_age.gdf["value"].astype(float) * scale
            imp = ImpactCalc(exp_age, ifs, hazard).impact(save_mat=False, assign_centroids=False)
            arr = np.asarray(imp.at_event, dtype=float)
            age_impacts[age] = arr
            daily_total += arr

        dates = self.dates_by_year[year]
        season_mask = _season_mask_by_md(dates, self.season_start_md, self.season_end_md)
        event_day_mask = np.diff(hazard.intensity.indptr) > 0 if self.use_extreme_track else np.zeros_like(daily_total, dtype=bool)

        pen = float(self.coverage_mean_for_mode(year, sample["ac_ssp"], mode=ac_mode))
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
            "ramp_factor": ramp_factor,
            "pop_total": self.pop_total_for_exposure(exp_path, scale),
        }

    def evaluate_branch_anchors(
        self,
        sample: dict[str, Any],
        *,
        ac_mode: str = "base",
        wh_mode: str | None = None,
        tree_enabled: bool = True,
        ews_enabled: bool = False,
        extreme_threshold_c: float | None = None,
        extreme_min_duration_days: int | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, float], float | None]:
        anchor_results: dict[int, dict[str, Any]] = {}
        pop_totals: dict[int, float] = {}

        if not ews_enabled:
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
                    extreme_threshold_c=extreme_threshold_c,
                    extreme_min_duration_days=extreme_min_duration_days,
                )
                anchor_results[year] = res
                pop_totals[year] = res["pop_total"]
            return anchor_results, pop_totals, None

        if self.ews_uses_event_mask_warning():
            for year in self.years:
                res = self.evaluate_year(
                    year,
                    sample,
                    threshold_ref=None,
                    pop_totals={},
                    ac_mode=ac_mode,
                    wh_mode=wh_mode,
                    tree_enabled=tree_enabled,
                    ews_enabled=True,
                    extreme_threshold_c=extreme_threshold_c,
                    extreme_min_duration_days=extreme_min_duration_days,
                )
                anchor_results[year] = res
                pop_totals[year] = res["pop_total"]
            return anchor_results, pop_totals, None

        ref_year = self.ews_threshold_ref_year if self.ews_threshold_ref_year in self.years else min(self.years)
        eval_order = [ref_year] + [y for y in self.years if y != ref_year]
        prelim_ref = self.evaluate_year(
            ref_year,
            sample,
            threshold_ref=None,
            pop_totals={},
            ac_mode=ac_mode,
            wh_mode=wh_mode,
            tree_enabled=tree_enabled,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration_days,
        )
        anchor_results[ref_year] = prelim_ref
        pop_totals[ref_year] = prelim_ref["pop_total"]
        season_ref = _season_mask_by_md(self.dates_by_year[ref_year], self.season_start_md, self.season_end_md)
        threshold_ref = _safe_quantile_threshold(prelim_ref["daily_base_total"][season_ref], sample["ews_target_days"])

        anchor_results[ref_year] = self.evaluate_year(
            ref_year,
            sample,
            threshold_ref=threshold_ref,
            pop_totals={ref_year: prelim_ref["pop_total"]},
            ac_mode=ac_mode,
            wh_mode=wh_mode,
            tree_enabled=tree_enabled,
            ews_enabled=True,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration_days,
        )
        pop_totals[ref_year] = anchor_results[ref_year]["pop_total"]
        for year in eval_order[1:]:
            res = self.evaluate_year(
                year,
                sample,
                threshold_ref=threshold_ref,
                pop_totals={**pop_totals, year: 0.0},
                ac_mode=ac_mode,
                wh_mode=wh_mode,
                tree_enabled=tree_enabled,
                ews_enabled=True,
                extreme_threshold_c=extreme_threshold_c,
                extreme_min_duration_days=extreme_min_duration_days,
            )
            anchor_results[year] = res
            pop_totals[year] = res["pop_total"]

        for year in self.years:
            anchor_results[year] = self.evaluate_year(
                year,
                sample,
                threshold_ref=threshold_ref,
                pop_totals=pop_totals,
                ac_mode=ac_mode,
                wh_mode=wh_mode,
                tree_enabled=tree_enabled,
                ews_enabled=True,
                extreme_threshold_c=extreme_threshold_c,
                extreme_min_duration_days=extreme_min_duration_days,
            )
        return anchor_results, pop_totals, threshold_ref

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
            "if_family": IF_FAMILIES[int(row["IF_FAMILY_IDX"])],
            "t_ref_C": TREF_OPTIONS[int(row["IF_TREF_IDX"])],
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
            "ac_capex_per_user": [350.0, 500.0, 650.0][int(row["AC_CAPEX_PER_USER_IDX"])],
            "ac_tariff_eur_per_kwh": self.ac_tariff_options[int(row["AC_TARIFF_EUR_PER_KWH_IDX"])],
            "ac_lifetime_years": [8, 10, 12][int(row["AC_LIFETIME_YEARS_IDX"])],
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

    def compute_ac_cost_metrics(
        self,
        sample: dict[str, Any],
        years_all: np.ndarray,
        pop_25y: np.ndarray,
    ) -> dict[str, float]:
        r = float(sample["discount_rate"])
        t_index = np.arange(len(years_all), dtype=float)
        maint_rate = float(self.ac_cost_params.get("maint_rate", self.ac_cfg.get("maint_rate", 0.05)))
        maint_per_user_yr = float(
            self.ac_cost_params.get(
                "maint_per_user_yr",
                maint_rate * float(sample["ac_capex_per_user"]),
            )
        )
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

        pv_capex = _pv_capex_with_replacements(
            new_users_t,
            float(sample["ac_capex_per_user"]),
            int(sample["ac_lifetime_years"]),
            r,
        )
        pv_maint = float(np.sum((np.maximum(added_users_t, 0.0) * maint_per_user_yr) / discount_factors))

        kwh_inc_standalone_t = kwh_policy_t - kwh_base_t
        kwh_inc_with_trees_t = kwh_policy_with_trees_t - kwh_base_with_trees_t

        pv_elec_standalone = float(np.sum((np.maximum(kwh_inc_standalone_t, 0.0) * tariff) / discount_factors))
        pv_elec_with_trees = float(np.sum((np.maximum(kwh_inc_with_trees_t, 0.0) * tariff) / discount_factors))
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
        }

    def compute_tree_cost_metrics(self, sample: dict[str, Any]) -> dict[str, float]:
        trees_cfg = self.cfg.get("trees", {})
        years = HORIZON_YEARS
        r = float(sample["discount_rate"])

        base_capex_per_tree = float(
            self.tree_cost_params.get("capex_per_tree", trees_cfg.get("capex_per_tree_eur", 0.0))
        )
        base_capex_per_index = float(
            self.tree_cost_params.get(
                "capex_per_index_pt",
                trees_cfg.get("capex_per_index_pt_eur", 0.0),
            )
        )
        # UQ scales the CALIBRATED per-GVI-point CAPEX by a dimensionless multiplier (0.8/1.0/1.2).
        capex_scale = float(sample["tree_capex_mult"])
        capex_per_index = base_capex_per_index * capex_scale

        base_om_per_tree = float(
            self.tree_cost_params.get("om_per_tree_yr", trees_cfg.get("om_per_tree_per_year_eur", 0.0))
        )
        # O&M per GVI-point: calibrated om_per_index_pt_yr if given, else the calibrated per-tree
        # O&M/CAPEX ratio applied to the per-GVI-point CAPEX (0.0 if per-tree costs are absent).
        base_om_per_index = float(
            self.tree_cost_params.get(
                "om_per_index_pt_yr",
                (base_om_per_tree / base_capex_per_tree) * base_capex_per_index if (base_capex_per_tree > 0 and base_capex_per_index > 0) else 0.0,
            )
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
        lifetime = int(self.tree_cost_params.get("lifetime_years", trees_cfg.get("lifetime_years", HORIZON_YEARS)))
        pv_capex = _npv_capex_linear(delta_index_total, years, r, capex_per_index)
        pv_om, _ = _npv_om_cohorts_scaled(
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
        }

    def compute_lambda_y(self, sample: dict[str, Any], years_all: np.ndarray) -> np.ndarray:
        """Compute lambda_y: tree-cooled AC utilization scaling factor for waste heat.

        lambda_y = s_tree(y) / s_base(y), where s = CDD-based activity share.
        Trees cool the urban canopy → lower hazard → reduced AC utilization → less waste heat.
        """
        T = len(years_all)
        lambda_y = np.ones(T, dtype=float)

        ramp_years = int(sample["tree_ramp_years"])
        start_age = int(sample["tree_start_age"])
        tree_scale = float(sample.get("TREE_COEFF_SCALE", 1.0))

        dT2M_monthly = self.tree_dT2M_citymean_monthly * tree_scale  # scale by UQ coefficient

        anchor_lambdas = {}
        for year in self.years:
            citymean = self.ref_citymean_by_year.get(year)
            if citymean is None:
                continue
            months = self.months_by_year.get(year)
            if months is None:
                continue

            ys = year - int(years_all[0])
            maturity = min(max((ys + start_age) / ramp_years, 0.0), 1.0)

            # Apply monthly tree cooling scaled by maturity
            dT_daily = np.array([dT2M_monthly[m - 1] * maturity for m in months], dtype=float)
            T_tree = citymean.astype(float) + dT_daily  # cooling (negative dT)

            s_base = self.waste_heat_activity_share(citymean)
            s_tree = self.waste_heat_activity_share(T_tree)

            anchor_lambdas[year] = np.clip(s_tree / max(s_base, 1e-9), 0.0, 1.0)

        if anchor_lambdas:
            anchor_yrs = np.array(sorted(anchor_lambdas.keys()), dtype=float)
            anchor_vals = np.array([anchor_lambdas[int(y)] for y in anchor_yrs], dtype=float)
            lambda_y = np.interp(years_all.astype(float), anchor_yrs, anchor_vals)
            lambda_y = np.clip(lambda_y, 0.0, 1.0)

        return lambda_y

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

        anchor_results, pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            tree_enabled=True,
            ews_enabled=True,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )

        sample_year = int(sample["year"])
        year_res = anchor_results[sample_year]
        residual_daily = year_res["daily_residual_total"]
        impact_freq = _freq_curve_from_daily(residual_daily, RETURN_PERIODS)
        annual_deaths = float(residual_daily.sum())
        aai_agg = annual_deaths / float(self.days_in_year(sample_year))

        anchor_years = np.array(self.years, dtype=int)
        years_all = np.arange(min(self.years), min(self.years) + HORIZON_YEARS, dtype=int)
        t_index = years_all - years_all[0]

        warning_days_anchor = np.array([anchor_results[y]["warning_days"] for y in self.years], dtype=float)
        deaths_warning_anchor = np.array([anchor_results[y]["deaths_on_warning_days"] for y in self.years], dtype=float)
        gross_anchor = np.array([anchor_results[y]["gross_avoided"] for y in self.years], dtype=float)
        net_anchor = np.array([anchor_results[y]["net_avoided"] for y in self.years], dtype=float)
        lys_anchor = np.array([anchor_results[y]["life_years_saved"] for y in self.years], dtype=float)
        pop_anchor = np.array([pop_totals[y] for y in self.years], dtype=float)

        warning_days_25y = _interp_1d_years(anchor_years, warning_days_anchor, years_all)
        deaths_warning_25y = _interp_1d_years(anchor_years, deaths_warning_anchor, years_all)
        gross_25y = _interp_1d_years(anchor_years, gross_anchor, years_all)
        net_25y = _interp_1d_years(anchor_years, net_anchor, years_all)
        lys_25y = _interp_1d_years(anchor_years, lys_anchor, years_all)
        pop_25y = _interp_1d_years(anchor_years, pop_anchor, years_all)

        ramp_25y = np.array([self.ramp_factor(y, sample["ews_ramp_years"]) for y in years_all], dtype=float)
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
            **impact_freq,
            "sample_warning_days": float(year_res["warning_days"]),
            "sample_threshold_deaths_per_day": float(year_res["threshold"]),
            "sample_deaths_on_warning_days": float(year_res["deaths_on_warning_days"]),
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

        ref_anchor_results, ref_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            wh_mode="base",
            tree_enabled=False,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ac_gross_anchor_results, ac_gross_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="policy",
            wh_mode="base",
            tree_enabled=False,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ac_net_anchor_results, ac_net_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="policy",
            wh_mode="policy",
            tree_enabled=False,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        tree_anchor_results, tree_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            wh_mode="base",
            tree_enabled=True,
            ews_enabled=False,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )
        ews_policy_anchor_results, ews_policy_pop_totals, _ = self.evaluate_branch_anchors(
            sample,
            ac_mode="base",
            wh_mode="base",
            tree_enabled=False,
            ews_enabled=True,
            extreme_threshold_c=extreme_threshold_c,
            extreme_min_duration_days=extreme_min_duration,
        )

        _, ref_annual_25y, ref_pop_25y, _ = self.interpolate_branch_annuals(ref_anchor_results, ref_pop_totals)
        _, ac_gross_annual_25y, _, _ = self.interpolate_branch_annuals(ac_gross_anchor_results, ac_gross_pop_totals)
        _, ac_net_annual_25y, _, _ = self.interpolate_branch_annuals(ac_net_anchor_results, ac_net_pop_totals)
        _, tree_annual_25y, _, _ = self.interpolate_branch_annuals(tree_anchor_results, tree_pop_totals)
        _, ews_policy_annual_25y, _, _ = self.interpolate_branch_annuals(ews_policy_anchor_results, ews_policy_pop_totals)

        ac_gross_avoided_25y = ref_annual_25y - ac_gross_annual_25y
        ac_net_avoided_25y = ref_annual_25y - ac_net_annual_25y
        ac_penalty_raw_25y = ac_gross_avoided_25y - ac_net_avoided_25y  # standalone AC (no trees)
        tree_avoided_25y = ref_annual_25y - tree_annual_25y
        ews_reference_avoided_25y = ref_annual_25y - ews_policy_annual_25y

        # Lambda_y: tree-cooled waste-heat correction for the explicit AC+trees interaction branch.
        lambda_y_25y = self.compute_lambda_y(sample, years_all)
        ac_penalty_with_trees_25y = ac_penalty_raw_25y * lambda_y_25y
        ac_net_with_trees_25y = ac_gross_avoided_25y - ac_penalty_with_trees_25y

        # AC costs: standalone AC plus explicit AC+trees interaction on electricity costs.
        ac_costs = self.compute_ac_cost_metrics(sample, years_all, ref_pop_25y)
        tree_costs = self.compute_tree_cost_metrics(sample)
        ac_gross_avoided_cum = float(ac_gross_avoided_25y.sum())
        ac_net_avoided_cum = float(ac_net_avoided_25y.sum())
        ac_net_with_trees_cum = float(ac_net_with_trees_25y.sum())
        tree_avoided_cum = float(tree_avoided_25y.sum())

        result.update(ac_costs)
        result.update(
            {
                "ac_gross_avoided_deaths_25y_cum": ac_gross_avoided_cum,
                "ac_net_avoided_deaths_25y_cum": ac_net_avoided_cum,
                "ac_waste_heat_penalty_25y_cum": float(ac_penalty_raw_25y.sum()),
                "ac_waste_heat_penalty_raw_25y_cum": float(ac_penalty_raw_25y.sum()),
                "ac_cost_per_gross_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_25y"], ac_gross_avoided_cum),
                "ac_cost_per_net_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_25y"], ac_net_avoided_cum),
                "ac_with_trees_net_avoided_deaths_25y_cum": ac_net_with_trees_cum,
                "ac_with_trees_waste_heat_penalty_25y_cum": float(ac_penalty_with_trees_25y.sum()),
                "ac_with_trees_cost_per_gross_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_with_trees_25y"], ac_gross_avoided_cum),
                "ac_with_trees_cost_per_net_death_25y_cum": _safe_ratio(ac_costs["ac_pv_cost_with_trees_25y"], ac_net_with_trees_cum),
                "lambda_y_mean": float(lambda_y_25y.mean()),
            }
        )
        result.update(tree_costs)
        result.update(
            {
                "tree_avoided_deaths_25y_cum": tree_avoided_cum,
                "tree_cost_per_death_25y_cum": _safe_ratio(tree_costs["tree_pv_cost_25y"], tree_avoided_cum),
            }
        )

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
            "tree_policy": tree_annual_25y,
            "ews_policy": ews_policy_annual_25y,
        }
        result["_policy_branch_effects"] = {
            "ac_gross_avoided_25y": ac_gross_avoided_25y,
            "ac_net_avoided_25y": ac_net_avoided_25y,
            "ac_net_with_trees_25y": ac_net_with_trees_25y,
            "ac_penalty_raw_25y": ac_penalty_raw_25y,
            "ac_penalty_with_trees_25y": ac_penalty_with_trees_25y,
            "tree_avoided_25y": tree_avoided_25y,
            "ews_reference_avoided_25y": ews_reference_avoided_25y,
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
        result["_anchor_results"] = anchor_results
        result["_years_all"] = years_all
        result["_warning_days_25y"] = warning_days_25y
        result["_deaths_warning_25y"] = deaths_warning_25y
        result["_net_25y"] = net_25y
        result["_cost_pv_25y"] = cost_pv_25y
        return result

    def run(self, n: int, seed: int = SEED_DEFAULT) -> dict[str, Path]:
        raw_samples, x = self.sample_parameters(n, seed)
        sample_rows: list[dict[str, Any]] = []
        impact_rows: list[dict[str, Any]] = []
        cba_ews_rows: list[dict[str, Any]] = []
        cba_ac_rows: list[dict[str, Any]] = []
        cba_tree_rows: list[dict[str, Any]] = []
        vuln_rows: list[dict[str, Any]] = []

        for idx, raw_row in raw_samples.iterrows():
            out = self.evaluate_sample(raw_row)
            decoded = self.sample_dict(raw_row)
            sample_row = {**raw_row.to_dict(), **{k: v for k, v in decoded.items() if k not in raw_row.to_dict()}, **{k: v for k, v in out.items() if not str(k).startswith("_")}}
            sample_rows.append(sample_row)
            impact_rows.append(
                {
                    "aai_agg": out["aai_agg"],
                    "annual_deaths": out["annual_deaths"],
                    **{f"rp{rp}": out[f"rp{rp}"] for rp in RETURN_PERIODS},
                }
            )
            cba_ews_rows.append(
                {
                    "ews_pv_cost_25y": out["ews_pv_cost_25y"],
                    "ews_net_avoided_deaths_25y_cum": out["ews_net_avoided_deaths_25y_cum"],
                    "ews_net_avoided_deaths_25y_pv": out["ews_net_avoided_deaths_25y_pv"],
                    "ews_cost_per_net_death_25y_cum": out["ews_cost_per_net_death_25y_cum"],
                    "ews_cost_per_net_death_25y_pv": out["ews_cost_per_net_death_25y_pv"],
                    "ews_life_years_saved_25y_cum": out["ews_life_years_saved_25y_cum"],
                }
            )
            cba_ac_rows.append(
                {
                    "ac_pv_capex_25y": out["ac_pv_capex_25y"],
                    "ac_pv_maint_25y": out["ac_pv_maint_25y"],
                    "ac_pv_elec_25y": out["ac_pv_elec_25y"],
                    "ac_pv_elec_with_trees_25y": out["ac_pv_elec_with_trees_25y"],
                    "ac_pv_cost_25y": out["ac_pv_cost_25y"],
                    "ac_pv_cost_with_trees_25y": out["ac_pv_cost_with_trees_25y"],
                    "ac_added_users_final": out["ac_added_users_final"],
                    "ac_gross_avoided_deaths_25y_cum": out["ac_gross_avoided_deaths_25y_cum"],
                    "ac_net_avoided_deaths_25y_cum": out["ac_net_avoided_deaths_25y_cum"],
                    "ac_waste_heat_penalty_25y_cum": out["ac_waste_heat_penalty_25y_cum"],
                    "ac_cost_per_gross_death_25y_cum": out["ac_cost_per_gross_death_25y_cum"],
                    "ac_cost_per_net_death_25y_cum": out["ac_cost_per_net_death_25y_cum"],
                    "ac_with_trees_net_avoided_deaths_25y_cum": out["ac_with_trees_net_avoided_deaths_25y_cum"],
                    "ac_with_trees_waste_heat_penalty_25y_cum": out["ac_with_trees_waste_heat_penalty_25y_cum"],
                    "ac_with_trees_cost_per_net_death_25y_cum": out["ac_with_trees_cost_per_net_death_25y_cum"],
                }
            )
            cba_tree_rows.append(
                {
                    "tree_pv_capex_25y": out["tree_pv_capex_25y"],
                    "tree_pv_om_25y": out["tree_pv_om_25y"],
                    "tree_pv_cost_25y": out["tree_pv_cost_25y"],
                    "tree_avoided_deaths_25y_cum": out["tree_avoided_deaths_25y_cum"],
                    "tree_cost_per_death_25y_cum": out["tree_cost_per_death_25y_cum"],
                    "tree_elec_pv_savings_base_users": out["tree_elec_pv_savings_base_users"],
                    "tree_elec_pv_savings_all_users": out["tree_elec_pv_savings_all_users"],
                    "tree_elec_kwh_base_users_25y": out["tree_elec_kwh_base_users_25y"],
                    "tree_elec_kwh_all_users_25y": out["tree_elec_kwh_all_users_25y"],
                    "tree_elec_co2_base_users_t_25y": out["tree_elec_co2_base_users_t_25y"],
                    "tree_elec_co2_all_users_t_25y": out["tree_elec_co2_all_users_t_25y"],
                    "tree_elec_cost_coverage_base_users_pct": out["tree_elec_cost_coverage_base_users_pct"],
                    "tree_elec_cost_coverage_all_users_pct": out["tree_elec_cost_coverage_all_users_pct"],
                    "elec_feedback_enabled": out["elec_feedback_enabled"],
                    "elec_coeff_scale": out["elec_coeff_scale"],
                }
            )
            vuln_rows.append({
                "sample_idx": idx,
                "year": decoded["year"],
                "exp_ssp": self.exp_ssp_options[int(raw_row["EXP_SSP_IDX"])] if self.exp_ssp_options else None,
                "VULN_K": float(raw_row["VULN_K"]),
                "VULN_PHI_2050": float(raw_row["VULN_PHI_2050"]),
                "VULN_DRMKC_SCALE_FB": float(raw_row["VULN_DRMKC_SCALE_FB"]),
                "VULN_DRMKC_SCALE_UE": float(raw_row["VULN_DRMKC_SCALE_UE"]),
                "VULN_GVI_SCALE_FB": float(raw_row["VULN_GVI_SCALE_FB"]),
                "VULN_GVI_SCALE_UE": float(raw_row["VULN_GVI_SCALE_UE"]),
                "VULN_RETROFIT_RATE": float(raw_row["VULN_RETROFIT_RATE"]),
                **{k: v for k, v in out.items() if str(k).startswith("vuln_")},
            })
            print(f"[{self.slug}] sample {idx + 1}/{n}: year={sample_row['year']} aai={out['aai_agg']:.3f}")

        samples_df = pd.DataFrame(sample_rows)
        impact_df = pd.DataFrame(impact_rows)
        cba_ews_df = pd.DataFrame(cba_ews_rows)
        cba_ac_df = pd.DataFrame(cba_ac_rows)
        cba_tree_df = pd.DataFrame(cba_tree_rows)
        vuln_df = pd.DataFrame(vuln_rows)

        sens_aai_df = _pawn_table(self.problem, x, {"aai_agg": samples_df["aai_agg"].to_numpy(float)})
        sens_freq_df = _pawn_table(
            self.problem,
            x,
            {f"rp{rp}": samples_df[f"rp{rp}"].to_numpy(float) for rp in RETURN_PERIODS},
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
        )
        self.make_figures(samples_df, sens_aai_df, sens_cba_df, sens_vuln_df)
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
            "samples": self.unc_dir / f"unc_samples_{self.slug}_improved.csv",
            "impact": self.unc_dir / f"unc_impact_summary_{self.slug}_improved.csv",
            "freq": self.unc_dir / f"unc_freq_curve_{self.slug}_improved.csv",
            "cba": self.unc_dir / f"unc_cba_ews_{self.slug}_improved.csv",
            "cba_ac": self.unc_dir / f"unc_cba_ac_{self.slug}_improved.csv",
            "cba_trees": self.unc_dir / f"unc_cba_trees_{self.slug}_improved.csv",
            "vuln": self.unc_dir / f"unc_vulnerability_{self.slug}_improved.csv",
            "sens_aai": self.unc_dir / f"sens_aai_agg_{self.slug}_improved.csv",
            "sens_freq": self.unc_dir / f"sens_freq_curve_{self.slug}_improved.csv",
            "sens_cba": self.unc_dir / f"sens_cba_ews_{self.slug}_improved.csv",
            "sens_cba_ac": self.unc_dir / f"sens_cba_ac_{self.slug}_improved.csv",
            "sens_cba_trees": self.unc_dir / f"sens_cba_trees_{self.slug}_improved.csv",
            "sens_vuln": self.unc_dir / f"sens_vulnerability_{self.slug}_improved.csv",
            "meta": self.unc_dir / f"uq_dimensions_{self.slug}_improved.json",
            "bundle": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved.h5",
            "bundle_sens": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved_with_sensitivity.h5",
        }
        samples_df.to_csv(paths["samples"], index=False)
        impact_df.to_csv(paths["impact"], index=False)
        impact_df[[f"rp{rp}" for rp in RETURN_PERIODS]].to_csv(paths["freq"], index=False)
        cba_ews_df.to_csv(paths["cba"], index=False)
        cba_ac_df.to_csv(paths["cba_ac"], index=False)
        cba_tree_df.to_csv(paths["cba_trees"], index=False)
        vuln_df.to_csv(paths["vuln"], index=False)
        sens_aai_df.to_csv(paths["sens_aai"], index=False)
        sens_freq_df.to_csv(paths["sens_freq"], index=False)
        sens_cba_df.to_csv(paths["sens_cba"], index=False)
        sens_cba_ac_df.to_csv(paths["sens_cba_ac"], index=False)
        sens_cba_tree_df.to_csv(paths["sens_cba_trees"], index=False)
        if not sens_vuln_df.empty:
            sens_vuln_df.to_csv(paths["sens_vuln"], index=False)

        meta = {
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
            "if_families": IF_FAMILIES,
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
                "ac_capex_per_user": [350.0, 500.0, 650.0],
                "ac_tariff_eur_per_kwh": self.ac_tariff_options,
                "ac_lifetime_years": [8, 10, 12],
                "tree_capex_mult": [0.8, 1.0, 1.2],
                "tree_om_mult": [1.0, 5.0],
            },
            "vuln_param_ranges": {
                "VULN_K": [0.55, 0.95],
                "VULN_PHI_2050": [0.50, 0.90],
                "VULN_DRMKC_SCALE_FB": [0.02, 0.08],
                "VULN_DRMKC_SCALE_UE": [0.04, 0.16],
                "VULN_GVI_SCALE_FB": [0.15, 0.55],
                "VULN_GVI_SCALE_UE": [0.25, 0.75],
                "VULN_RETROFIT_RATE": [0.005, 0.020],
            },
            "notes": [
                "Future T2M bands are sampled from across-GCM band tables, while tas uses avg(pct45,pct55) within each model upstream.",
                "Hazard structural modifiers are applied as spatially explicit daily raster adjustments for trees and waste heat.",
                ews_note,
                recalib_note,
                trigger_note,
                "When a sampled climate band is unavailable for a sampled scenario, the effective band is forced to central (e.g., ssp585 when only central is provided in the city delta table).",
                "CBA uncertainty now samples discounting plus AC/tree cost parameters in the same global sample as the impact-chain uncertainty.",
                "AC CBA is evaluated as policy AC versus current/autonomous AC under the same sampled hazard, exposure, IF and vulnerability settings.",
                "Tree CBA is evaluated as tree policy versus the same sampled no-tree reference branch.",
                "Vulnerability projection uncertainty (Level A): 7 parameters perturbed, SVI recomputed on-the-fly; output-only, does not affect mortality.",
                "Original March2026/NB09 outputs remain untouched; all improved artifacts are saved in tables/uncertainty_improved.",
            ],
        }
        with open(paths["meta"], "w") as f:
            json.dump(meta, f, indent=2)

        with pd.HDFStore(str(paths["bundle"]), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_ews_df
            store["cba_ac"] = cba_ac_df
            store["cba_trees"] = cba_tree_df
            store["vulnerability"] = vuln_df
        with pd.HDFStore(str(paths["bundle_sens"]), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_ews_df
            store["cba_ac"] = cba_ac_df
            store["cba_trees"] = cba_tree_df
            store["vulnerability"] = vuln_df
            store["sens_aai"] = sens_aai_df
            store["sens_freq"] = sens_freq_df
            store["sens_cba"] = sens_cba_df
            store["sens_cba_ac"] = sens_cba_ac_df
            store["sens_cba_trees"] = sens_cba_tree_df
            if not sens_vuln_df.empty:
                store["sens_vuln"] = sens_vuln_df
        return paths

    def make_figures(self, samples_df: pd.DataFrame, sens_aai_df: pd.DataFrame, sens_cba_df: pd.DataFrame, sens_vuln_df: pd.DataFrame | None = None) -> None:
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
        plt.savefig(self.unc_dir / f"unc_distribution_aai_agg_{self.slug}_improved.png", dpi=160)
        plt.close(fig)

        x, y = _ecdf(aai[np.isfinite(aai)])
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, color="#3A7CA5", lw=1.8)
        ax.set_title(f"{self.city} - aai_agg empirical CDF (improved)")
        ax.set_xlabel("aai_agg")
        ax.set_ylabel("ECDF")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_cdf_aai_agg_{self.slug}_improved.png", dpi=160)
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
        plt.savefig(self.unc_dir / f"sens_tornado_aai_agg_{self.slug}_improved.png", dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8, 4))
        cpd = samples_df["ews_cost_per_net_death_25y_cum"].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
        if cpd.size:
            ax.hist(cpd, bins=40, alpha=0.7, color="#8B5FBF", edgecolor="white")
        ax.set_title(f"{self.city} - EWS cost per net death (25y, improved)")
        ax.set_xlabel("EUR / net avoided death")
        ax.set_ylabel("Count")
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_distribution_cba_ews_{self.slug}_improved.png", dpi=160)
        plt.close(fig)

        x, y = _ecdf(cpd)
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, color="#8B5FBF", lw=1.8)
        ax.set_title(f"{self.city} - EWS cost per net death empirical CDF (improved)")
        ax.set_xlabel("EUR / net avoided death")
        ax.set_ylabel("ECDF")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_cdf_cba_ews_{self.slug}_improved.png", dpi=160)
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
            plt.savefig(self.unc_dir / f"sens_tornado_cba_ews_{self.slug}_improved.png", dpi=160)
            plt.close(fig)

        freq_cols = [f"rp{rp}" for rp in RETURN_PERIODS]
        freq_df = samples_df[freq_cols].replace([np.inf, -np.inf], np.nan)
        q05 = freq_df.quantile(0.05)
        q50 = freq_df.quantile(0.50)
        q95 = freq_df.quantile(0.95)
        x_rp = np.asarray(RETURN_PERIODS, dtype=float)
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        ax.fill_between(x_rp, q05.to_numpy(float), q95.to_numpy(float), color="#4C78A8", alpha=0.22, label="5-95%")
        ax.plot(x_rp, q50.to_numpy(float), marker="o", color="#1F4E79", lw=1.8, label="median")
        ax.set_title(f"{self.city} - uncertainty frequency curve (improved)")
        ax.set_xlabel("Return period (years)")
        ax.set_ylabel("Annual deaths")
        ax.set_xticks(x_rp)
        ax.grid(alpha=0.3)
        ax.legend(frameon=False)
        plt.tight_layout()
        plt.savefig(self.unc_dir / f"unc_freq_curve_{self.slug}_improved.png", dpi=160)
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
                plt.savefig(self.unc_dir / f"unc_distribution_vuln_svi_mean_{self.slug}_improved.png", dpi=160)
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
                plt.savefig(self.unc_dir / f"unc_distribution_vuln_2050_svi_mean_{self.slug}_improved.png", dpi=160)
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
                plt.savefig(self.unc_dir / f"sens_tornado_vuln_svi_gap_{self.slug}_improved.png", dpi=160)
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
                plt.savefig(self.unc_dir / f"sens_tornado_vuln_svi_mean_{self.slug}_improved.png", dpi=160)
                plt.close(fig)


def regenerate_saved_figures(city: str) -> Path:
    slug = city.strip().lower()
    runner = NB09Improved(slug)
    samples_df = pd.read_csv(runner.unc_dir / f"unc_samples_{runner.slug}_improved.csv")
    sens_aai_df = pd.read_csv(runner.unc_dir / f"sens_aai_agg_{runner.slug}_improved.csv")
    sens_cba_df = pd.read_csv(runner.unc_dir / f"sens_cba_ews_{runner.slug}_improved.csv")
    sens_vuln_path = runner.unc_dir / f"sens_vulnerability_{runner.slug}_improved.csv"
    sens_vuln_df = pd.read_csv(sens_vuln_path) if sens_vuln_path.exists() else pd.DataFrame()
    runner.make_figures(samples_df, sens_aai_df, sens_cba_df, sens_vuln_df)
    return runner.unc_dir


def run_nb09_improved(city: str | None = None, n: int | None = None, seed: int | None = None) -> dict[str, Path]:
    slug = (city or os.environ.get("CITY") or "rome").strip().lower()
    n_use = int(n if n is not None else os.environ.get("NB09_N", 512))
    seed_use = int(seed if seed is not None else os.environ.get("NB09_SEED", SEED_DEFAULT))
    runner = NB09Improved(slug)
    return runner.run(n=n_use, seed=seed_use)


def main() -> None:
    parser = argparse.ArgumentParser(description="Improved March2026 NB09 uncertainty workflow.")
    parser.add_argument("--city", default=os.environ.get("CITY", "rome"), help="City slug: rome, athens, lisbon")
    parser.add_argument("--n", type=int, default=int(os.environ.get("NB09_N", 512)), help="Latin hypercube sample size")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("NB09_SEED", SEED_DEFAULT)), help="Sampling seed")
    parser.add_argument("--figures-only", action="store_true", help="Regenerate saved figures from existing improved outputs")
    args = parser.parse_args()
    if args.figures_only:
        out_dir = regenerate_saved_figures(args.city)
        print(f"Regenerated improved figures in: {out_dir}")
    else:
        paths = run_nb09_improved(city=args.city, n=args.n, seed=args.seed)
        print("Saved improved uncertainty outputs in:")
        for key, path in paths.items():
            print(f"  {key}: {path}")


if __name__ == "__main__":
    main()

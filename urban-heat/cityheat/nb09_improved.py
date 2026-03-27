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
DISCOUNT_RATE = 0.03
SEED_DEFAULT = 42


def _resolve_root() -> Path:
    root = Path.cwd().resolve()
    while root != root.parent and not (root / "cityheat").exists():
        root = root.parent
    if not (root / "cityheat").exists():
        root = Path("/Users/armandeaboudrar-meda/Desktop/CMCC/URBADAPT/urban-heat")
    return root


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


def _pawn_table(problem: dict[str, Any], x: np.ndarray, metric_map: dict[str, np.ndarray]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for metric, y in metric_map.items():
        res = pawn.analyze(problem, x, np.asarray(y, dtype=float), S=10, seed=SEED_DEFAULT)
        for idx, param in enumerate(res["names"]):
            for si in ["minimum", "mean", "median", "maximum", "CV"]:
                rows.append({"si": si, "param": param, metric: float(res[si][idx])})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    out = None
    for metric in metric_map:
        sub = df[["si", "param", metric]].copy()
        out = sub if out is None else out.merge(sub, on=["si", "param"], how="outer")
    out["param2"] = np.nan
    cols = ["si", "param", "param2", *metric_map.keys()]
    return out[cols]


def _freq_curve_from_daily(daily_impacts: np.ndarray, rps: list[int]) -> dict[str, float]:
    vals = np.asarray(daily_impacts, dtype=float)
    out: dict[str, float] = {}
    for rp in rps:
        q = float(np.clip(1.0 - 1.0 / float(rp), 0.0, 1.0))
        out[f"rp{int(rp)}"] = float(np.quantile(vals, q)) if vals.size else np.nan
    return out


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
        self.base = self.root / "data" / self.city
        self.out = self.root / "outputs" / self.slug
        self.int_dir = self.out / "interim"
        self.tab_dir = self.out / "tables"
        self.unc_dir = self.tab_dir / "uncertainty_improved"
        self.unc_dir.mkdir(parents=True, exist_ok=True)

        self.exp_cache: dict[str, Exposures] = {}
        self.exp_age_cache: dict[tuple[str, str], Exposures] = {}
        self.if_block_cache: dict[tuple[str, int], dict[str, Any]] = {}

        self._load_core_artifacts()
        self._load_hazard_scaffold()
        self._load_climate_inputs()
        self._load_exposure_inputs()
        self._load_ac_inputs()
        self._load_ews_inputs()
        self._load_tree_inputs()
        self._build_param_specs()

    def P(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.base / p)

    def _find_first_existing(self, candidates: list[Path]) -> Path:
        for path in candidates:
            if path.exists():
                return path
        raise FileNotFoundError("Could not find any candidate file:\n" + "\n".join(str(p) for p in candidates))

    def _load_core_artifacts(self) -> None:
        self.template_tif = self.int_dir / "template_ref.tif"
        self.city_mask_npz = self.int_dir / "city_mask.npz"
        self.exp_manifest_path = self.int_dir / "exposure_manifest.json"
        self.if_jsons = {
            "burke_polynomial": self.int_dir / f"if_curves_by_year_{self.slug}.json",
            "burke_powerlaw": self.int_dir / f"if_curves_by_year_{self.slug}_powerlaw.json",
        }
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
        bands_table = self.P("T2MmeanDeltas/climate_change_provide_markups_bands.csv")
        legacy_table = self.P(
            self.cfg.get("files", {}).get("t2m_deltas_table", "T2MmeanDeltas/climate_change_provide_markups_avg.csv")
        )
        gcm_table = self.P("T2MmeanDeltas/climate_change_provide_markups_gcm.csv")

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

        self.wh_enabled_default = bool(self.wh_cfg.get("enabled", True))
        self.wh_enabled_options = _bool_options_with_baseline(
            self.wh_cfg.get("enabled_options", [False, True]), self.wh_enabled_default
        )
        self.wh_lut_options = list(self.wh_cfg.get("lut_case_options", ["low", "central", "high"]))
        self.wh_ratio_min, self.wh_ratio_max = map(float, self.wh_cfg.get("dailymean_from_night_range", [0.33, 0.67]))
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
        self.coverage_years = cov_years
        self.coverage_pattern_by_year: dict[int, np.ndarray] = {}
        for idx, y in enumerate(cov_years):
            arr = cov_base_3d[idx if cov_base_3d.shape[0] > 1 else 0]
            row_vals = arr.ravel()[self.row_cols].astype(np.float32)
            masked = row_vals[self.row_is_city]
            mean_val = float(masked.mean()) if masked.size else 0.0
            if mean_val <= 0:
                pattern = np.ones_like(row_vals, dtype=np.float32)
            else:
                pattern = np.zeros_like(row_vals, dtype=np.float32)
                pattern[self.row_is_city] = (masked / mean_val).astype(np.float32)
            self.coverage_pattern_by_year[int(y)] = pattern

        self.ac_cost_params_path = self._find_first_existing(
            [self.int_dir / f"ac_cost_params_{self.slug}.json", self.int_dir / f"ac_costs_{self.slug}.json"]
        )
        self.ac_cost_params = _load_json(self.ac_cost_params_path)

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
        self.ews_interp_options = ["marginal", "counterfactual"]
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
        self.threshold_meta = _load_json(self._find_first_existing(thr_candidates))

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
            ParamSpec("EWS_TARGET_DAYS_IDX", "choice", options=list(range(len(self.ews_target_days_options)))),
            ParamSpec("EWS_RECALIB_YEARS_IDX", "choice", options=list(range(len(self.ews_recalib_options)))),
            ParamSpec("EWS_COST_MODEL_IDX", "choice", options=list(range(len(self.ews_cost_model_options)))),
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

    def dT_night_from_penetration(self, pen: float, case: str) -> float:
        pen_points = np.array(sorted(self.wh_lut.keys()), dtype=float)
        dT_points = np.array([float(self.wh_lut[p][case]) for p in pen_points], dtype=float)
        return float(np.interp(np.clip(pen, 0.0, 1.0), pen_points, dT_points))

    def cop_amplification_factor(self, dT_night: float, cop_sens: float) -> float:
        alpha = float(cop_sens) * float(dT_night) * (1.0 + 1.0 / self.cop_ref) / self.cop_ref
        return 1.0 + alpha

    def interpolate_coverage_pattern(self, year: int) -> np.ndarray:
        year = int(year)
        if year in self.coverage_pattern_by_year:
            return self.coverage_pattern_by_year[year]
        anchor_years = np.array(sorted(self.coverage_pattern_by_year.keys()), dtype=int)
        stack = np.vstack([self.coverage_pattern_by_year[y][None, :] for y in anchor_years]).astype(np.float32)
        out = np.empty(self.n_city, dtype=np.float32)
        for idx in range(self.n_city):
            out[idx] = np.interp(year, anchor_years, stack[:, idx])
        return out

    def coverage_for_sample(self, year: int, ac_ssp: int) -> np.ndarray:
        pattern = self.interpolate_coverage_pattern(year)
        target_mean = self.get_penetration(ac_ssp, year)
        return _scale_pattern_to_mean_masked(pattern, self.row_is_city, target_mean, upper=0.98)

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
    ) -> ImpactFuncSet:
        block = self.load_if_block(family, year)
        pen = float(self.get_penetration(ac_ssp, year))
        ac_eff_map = self.cfg["efficacy_scenarios"][ac_eff_scen]
        funcs: list[ImpactFunc] = []
        for age in AGE_ORDER:
            rec = block[age]
            intensity = np.asarray(rec["intensity"], dtype=float) + (float(tref_c) - TREF_BASE)
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
                    intensity_unit="degC",
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

        coverage = self.coverage_for_sample(year, ac_ssp)
        pen = float(self.get_penetration(ac_ssp, year))
        dT_night = self.dT_night_from_penetration(pen, wh_case) if wh_enabled else 0.0
        if wh_enabled and cop_enabled:
            cop_sens = float(self.cop_sens.get(cop_case, self.cop_sens.get("central", 0.065)))
            amp = self.cop_amplification_factor(dT_night, cop_sens)
        else:
            amp = 1.0
        wh_city_scalar = float(wh_ratio) * float(dT_night) * float(amp) if wh_enabled else 0.0
        if wh_city_scalar != 0.0 and coverage.size:
            coverage_mean = float(coverage[self.row_is_city].mean())
            wh_pattern = coverage / max(coverage_mean, 1e-6)
        else:
            wh_pattern = np.zeros(len(self.row_cols), dtype=np.float32)

        maturity = self.tree_maturity_factor(year, tree_ramp_years, tree_start_age)
        cap_ratio = float(tree_cap_uplift) / max(self.tree_base_cap, 1e-6)
        tree_scale = float(tree_coeff_scale) * cap_ratio * float(maturity)

        for row in range(n_days):
            a, b = indptr[row], indptr[row + 1]
            data[a:b] += float(mode_adj[row] + clim_adj[row])
            if wh_city_scalar != 0.0:
                data[a:b] += (wh_city_scalar * wh_pattern).astype(np.float32)
            if tree_scale != 0.0:
                data[a:b] += (self.tree_month_maps[int(months[row]) - 1] * tree_scale).astype(np.float32)

        h = Hazard("T2M")
        h.haz_type = "T2M"
        h.units = "degC (daily mean)"
        h.centroids = self.centroids
        h.intensity = base
        h.fraction = sparse.csr_matrix(np.tile(self.mask_vec, (n_days, 1)))
        h.date = self.dates_by_year[year]
        h.event_id = np.array([int(pd.Timestamp(d).strftime("%Y%m%d")) for d in h.date], dtype=int)
        h.event_name = np.array([f"T2M_{str(pd.Timestamp(d).date())}" for d in h.date], dtype=object)
        h.frequency = np.full(n_days, 1.0 / self.days_in_year(year), dtype=float)
        h.frequency_unit = "1/year"
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
            wh_case=sample["wh_case"],
            wh_ratio=sample["WH_RATIO"],
            cop_case=sample["cop_case"],
            wh_enabled=sample["wh_enabled"],
            cop_enabled=sample["cop_enabled"],
            tree_coeff_scale=sample["TREE_COEFF_SCALE"],
            tree_cap_uplift=sample["TREE_CAP_UPLIFT"],
            tree_ramp_years=sample["tree_ramp_years"],
            tree_start_age=sample["tree_start_age"],
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

        pen = float(self.get_penetration(sample["ac_ssp"], year))
        overlap = float(self.ews_overlap.get(sample["ews_overlap_level"], self.ews_overlap.get("central", 0.3)))
        ac_penalty = float(np.clip(1.0 - overlap * pen, 0.0, 1.0))
        ramp_factor = self.ramp_factor(year, sample["ews_ramp_years"])

        if threshold_ref is None:
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

            if str(sample["ews_interpretation"]).lower() == "marginal":
                lvl = sample[f"ews_eff_{self._age_key(age)}_level"]
                eff_age = float(self.ews_marg.get(age, {}).get(lvl, self.ews_marg.get(age, {}).get("central", 0.0)))
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

    def _age_key(self, age: str) -> str:
        return {"<15": "lt15", "15-64": "15_64", "65+": "65p"}[age]

    def sample_dict(self, raw_row: pd.Series) -> dict[str, Any]:
        row = raw_row.to_dict()
        return {
            **row,
            "year": self.years[int(row["YEAR_IDX"])],
            "baseline_mode": self.baseline_modes[int(row["BASELINE_MODE_IDX"])],
            "clim_scen": self.clim_scens[int(row["CLIM_SCEN_IDX"])],
            "clim_band": self.clim_bands[int(row["CLIM_BAND_IDX"])],
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
            "ews_target_days": self.ews_target_days_options[int(row["EWS_TARGET_DAYS_IDX"])],
            "ews_recalib_years": self.ews_recalib_options[int(row["EWS_RECALIB_YEARS_IDX"])],
            "ews_cost_model": self.ews_cost_model_options[int(row["EWS_COST_MODEL_IDX"])],
        }

    def evaluate_sample(self, raw_row: pd.Series) -> dict[str, Any]:
        sample = self.sample_dict(raw_row)

        anchor_results: dict[int, dict[str, Any]] = {}
        pop_totals: dict[int, float] = {}
        ref_year = self.ews_threshold_ref_year if self.ews_threshold_ref_year in self.years else min(self.years)

        # Compute the reference-year burden first so threshold calibration responds to the sampled hazard + IF setup.
        eval_order = [ref_year] + [y for y in self.years if y != ref_year]
        prelim_ref = self.evaluate_year(ref_year, sample, threshold_ref=None, pop_totals={})
        anchor_results[ref_year] = prelim_ref
        pop_totals[ref_year] = prelim_ref["pop_total"]
        threshold_ref = _safe_quantile_threshold(
            prelim_ref["daily_base_total"][prelim_ref["daily_base_total"] >= 0][prelim_ref["warning_mask"] | _season_mask_by_md(self.dates_by_year[ref_year], self.season_start_md, self.season_end_md)],
            sample["ews_target_days"],
        )
        # The reference threshold uses only in-season days.
        season_ref = _season_mask_by_md(self.dates_by_year[ref_year], self.season_start_md, self.season_end_md)
        threshold_ref = _safe_quantile_threshold(prelim_ref["daily_base_total"][season_ref], sample["ews_target_days"])

        # Re-evaluate the reference year and all remaining anchors with the event-level warning logic.
        anchor_results[ref_year] = self.evaluate_year(ref_year, sample, threshold_ref=threshold_ref, pop_totals={ref_year: prelim_ref["pop_total"]})
        pop_totals[ref_year] = anchor_results[ref_year]["pop_total"]
        for year in eval_order[1:]:
            res = self.evaluate_year(year, sample, threshold_ref=threshold_ref, pop_totals={**pop_totals, year: 0.0})
            anchor_results[year] = res
            pop_totals[year] = res["pop_total"]

        # Reapply stepwise threshold scaling now that all anchor populations are known.
        for year in self.years:
            if year == ref_year:
                anchor_results[year] = self.evaluate_year(year, sample, threshold_ref=threshold_ref, pop_totals=pop_totals)
            else:
                anchor_results[year] = self.evaluate_year(year, sample, threshold_ref=threshold_ref, pop_totals=pop_totals)

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
        discount_factors = (1.0 + DISCOUNT_RATE) ** t_index
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
        cba_rows: list[dict[str, Any]] = []

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
            cba_rows.append(
                {
                    "ews_pv_cost_25y": out["ews_pv_cost_25y"],
                    "ews_net_avoided_deaths_25y_cum": out["ews_net_avoided_deaths_25y_cum"],
                    "ews_net_avoided_deaths_25y_pv": out["ews_net_avoided_deaths_25y_pv"],
                    "ews_cost_per_net_death_25y_cum": out["ews_cost_per_net_death_25y_cum"],
                    "ews_cost_per_net_death_25y_pv": out["ews_cost_per_net_death_25y_pv"],
                    "ews_life_years_saved_25y_cum": out["ews_life_years_saved_25y_cum"],
                }
            )
            print(f"[{self.slug}] sample {idx + 1}/{n}: year={sample_row['year']} aai={out['aai_agg']:.3f}")

        samples_df = pd.DataFrame(sample_rows)
        impact_df = pd.DataFrame(impact_rows)
        cba_df = pd.DataFrame(cba_rows)

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
        )

        paths = self.save_outputs(samples_df, impact_df, cba_df, sens_aai_df, sens_freq_df, sens_cba_df)
        self.make_figures(samples_df, sens_aai_df, sens_cba_df)
        return paths

    def save_outputs(
        self,
        samples_df: pd.DataFrame,
        impact_df: pd.DataFrame,
        cba_df: pd.DataFrame,
        sens_aai_df: pd.DataFrame,
        sens_freq_df: pd.DataFrame,
        sens_cba_df: pd.DataFrame,
    ) -> dict[str, Path]:
        paths = {
            "samples": self.unc_dir / f"unc_samples_{self.slug}_improved.csv",
            "impact": self.unc_dir / f"unc_impact_summary_{self.slug}_improved.csv",
            "freq": self.unc_dir / f"unc_freq_curve_{self.slug}_improved.csv",
            "cba": self.unc_dir / f"unc_cba_ews_{self.slug}_improved.csv",
            "sens_aai": self.unc_dir / f"sens_aai_agg_{self.slug}_improved.csv",
            "sens_freq": self.unc_dir / f"sens_freq_curve_{self.slug}_improved.csv",
            "sens_cba": self.unc_dir / f"sens_cba_ews_{self.slug}_improved.csv",
            "meta": self.unc_dir / f"uq_dimensions_{self.slug}_improved.json",
            "bundle": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved.h5",
            "bundle_sens": self.unc_dir / f"unc_impact_{self.slug}_march2026_improved_with_sensitivity.h5",
        }
        samples_df.to_csv(paths["samples"], index=False)
        impact_df.to_csv(paths["impact"], index=False)
        impact_df[[f"rp{rp}" for rp in RETURN_PERIODS]].to_csv(paths["freq"], index=False)
        cba_df.to_csv(paths["cba"], index=False)
        sens_aai_df.to_csv(paths["sens_aai"], index=False)
        sens_freq_df.to_csv(paths["sens_freq"], index=False)
        sens_cba_df.to_csv(paths["sens_cba"], index=False)

        meta = {
            "city": self.city,
            "slug": self.slug,
            "years": self.years,
            "baseline_modes": self.baseline_modes,
            "climate_scenarios": self.clim_scens,
            "climate_bands": self.clim_bands,
            "climate_source_options": self.clim_source_options,
            "gcm_options": self.gcm_options,
            "if_families": IF_FAMILIES,
            "t_ref_options": TREF_OPTIONS,
            "ac_ssp_options": self.ac_ssp_options,
            "exp_ssp_options": self.exp_ssp_options,
            "ac_efficacy_scenarios": self.efficacy_scenarios,
            "ews_interp_options": self.ews_interp_options,
            "ews_level_options": self.level_options,
            "ews_target_days_options": self.ews_target_days_options,
            "ews_recalib_options": self.ews_recalib_options,
            "wh_enabled_options": self.wh_enabled_options,
            "cop_enabled_options": self.cop_enabled_options,
            "tree_ramp_options": self.tree_ramp_options,
            "tree_start_age_options": self.tree_start_age_options,
            "notes": [
                "Future T2M bands are sampled from across-GCM band tables, while tas uses avg(pct45,pct55) within each model upstream.",
                "Hazard structural modifiers are applied as spatially explicit daily raster adjustments for trees and waste heat.",
                "EWS is applied with event-level warning-day logic calibrated on the sampled reference-year mortality distribution.",
                "Threshold recalibration is represented as stepwise population-scaled updates at the sampled interval.",
                "CBA uncertainty currently adds the sampled EWS 25-year cost model and cost-effectiveness outputs next to the impact outputs.",
                "Original March2026/NB09 outputs remain untouched; all improved artifacts are saved in tables/uncertainty_improved.",
            ],
        }
        with open(paths["meta"], "w") as f:
            json.dump(meta, f, indent=2)

        with pd.HDFStore(str(paths["bundle"]), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_df
        with pd.HDFStore(str(paths["bundle_sens"]), mode="w") as store:
            store["samples"] = samples_df
            store["impact"] = impact_df
            store["cba_ews"] = cba_df
            store["sens_aai"] = sens_aai_df
            store["sens_freq"] = sens_freq_df
            store["sens_cba"] = sens_cba_df
        return paths

    def make_figures(self, samples_df: pd.DataFrame, sens_aai_df: pd.DataFrame, sens_cba_df: pd.DataFrame) -> None:
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


def regenerate_saved_figures(city: str) -> Path:
    slug = city.strip().lower()
    runner = NB09Improved(slug)
    samples_df = pd.read_csv(runner.unc_dir / f"unc_samples_{runner.slug}_improved.csv")
    sens_aai_df = pd.read_csv(runner.unc_dir / f"sens_aai_agg_{runner.slug}_improved.csv")
    sens_cba_df = pd.read_csv(runner.unc_dir / f"sens_cba_ews_{runner.slug}_improved.csv")
    runner.make_figures(samples_df, sens_aai_df, sens_cba_df)
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

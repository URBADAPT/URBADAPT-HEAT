"""Runtime, per-city resolution of the trees electricity co-benefit rate.

``electricity_feedback.pct_reduction_per_gvi_point`` is the % of summer AC electricity
saved per Green-View-Index point. Falchetta et al. (2026) Fig-5 makes it a function of the
city's summer temperature (hotter city -> more AC -> more saving per GVI point). The configs
ship a flat placeholder (0.008); this module computes the **city-specific** value at runtime
from the city's JJA (Jun-Aug) mean daily-MAXIMUM 2 m temperature, so a plain 01-08 run
calibrates it directly -- no separate calibration pass.

Tmax source: UrbClim ``UrbClim/UrbClimT2Mmax/T2M_year_daily_max_YYYY.nc`` (NB01 fetches it,
mirroring the daily-mean). If that product is absent, or ``electricity_feedback`` sets
``pct_reduction_source: config`` (a deliberate manual override), it falls back to the config
value -- so behaviour degrades gracefully rather than crashing.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Falchetta 2026 Fig-5 anchors: (JJA mean daily-max T2M degC, % AC reduction per GVI point).
# Matches calibration/gen_gvi_reduction.py; clamp to [20, 35] (no extrapolation, floor 0 <20).
_FIG5_ANCHORS = [(20.0, 0.000), (25.0, 0.001), (30.0, 0.008), (35.0, 0.020)]


def fig5_pct_from_tmax(tmax_c: float) -> float:
    """Piecewise-linear Falchetta Fig-5 reduction/GVI-point at a JJA daily-max temperature."""
    t = max(_FIG5_ANCHORS[0][0], min(_FIG5_ANCHORS[-1][0], float(tmax_c)))
    for (t0, v0), (t1, v1) in zip(_FIG5_ANCHORS, _FIG5_ANCHORS[1:]):
        if t <= t1:
            return float(v0 + (v1 - v0) * (t - t0) / (t1 - t0))
    return float(_FIG5_ANCHORS[-1][1])


def city_jja_dailymax_tmax(cfg: dict, base_dir, int_dir) -> float | None:
    """JJA (Jun-Aug) mean of the city-mean daily-MAX T2M over the baseline years, or None.

    Reads UrbClim ``UrbClimT2Mmax/T2M_year_daily_max_YYYY.nc`` (sibling of the daily-mean
    dir), city-masked by ``interim/city_mask.npz`` when available (else grid-mean).
    """
    api = (cfg.get("climate", {}) or {}).get("urbclim_api", {}) or {}
    years = set(int(y) for y in api.get("years", range(2008, 2018)))
    mean_rel = str(api.get("local_dir", "UrbClim/UrbClimT2Mmean"))
    max_dir = Path(base_dir) / mean_rel.replace("T2Mmean", "T2Mmax")
    files = []
    for f in glob.glob(str(max_dir / "T2M_year_daily_max_*.nc")):
        m = re.search(r"(\d{4})", Path(f).stem)
        if m and int(m.group(1)) in years:
            files.append(f)
    if not files:
        return None
    try:
        import xarray as xr
    except Exception:
        return None
    mask = None
    cmp = Path(int_dir) / "city_mask.npz"
    if cmp.exists():
        try:
            mask = np.load(cmp)["city_mask"].astype(bool)
        except Exception:
            mask = None
    try:
        with xr.open_mfdataset(sorted(files), combine="by_coords", decode_times=True) as ds:
            var = "T2M" if "T2M" in ds.data_vars else list(ds.data_vars)[0]
            da = ds[var]
            if mask is not None and mask.shape == da.isel(time=0).shape:
                da = da.where(xr.DataArray(mask, dims=("y", "x")))
            citymean = da.mean(dim=("y", "x"), skipna=True).to_numpy().astype(float)
            months = pd.to_datetime(da["time"].values).month
    except Exception:
        return None
    if np.nanmedian(citymean) > 150:  # Kelvin -> Celsius
        citymean = citymean - 273.15
    jja = citymean[np.isin(months, (6, 7, 8))]
    jja = jja[np.isfinite(jja)]
    return float(np.mean(jja)) if jja.size else None


def resolve_pct_gvi_reduction(cfg: dict, base_dir, int_dir) -> tuple[float, str]:
    """Per-city ``pct_reduction_per_gvi_point`` for the trees electricity co-benefit.

    Returns ``(value, provenance)``. Computes it from the city's JJA daily-max T2M via
    Falchetta Fig-5 at runtime; falls back to the config value when the T2M daily-max is
    absent or ``electricity_feedback.pct_reduction_source == 'config'``.
    """
    elec = cfg.get("electricity_feedback", {}) or {}
    fallback = float(elec.get("pct_reduction_per_gvi_point", 0.008))
    if str(elec.get("pct_reduction_source", "auto")).strip().lower() == "config":
        return fallback, f"config override ({fallback})"
    tmax = city_jja_dailymax_tmax(cfg, base_dir, int_dir)
    if tmax is None:
        return fallback, (f"config fallback ({fallback}); no UrbClimT2Mmax "
                          "-> run NB01 (fetches T2M daily-max) or calibration/fetch_urbclim_t2mmax.py")
    return fig5_pct_from_tmax(tmax), f"Falchetta Fig-5 @ JJA daily-max T2M={tmax:.2f}C (runtime, per-city)"

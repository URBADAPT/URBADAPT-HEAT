"""AC electricity use and AC waste-heat warming: city-agnostic parameters and formulas.

This module is the one place where these choices live. Notebook 05 (deterministic run),
Notebook 08 (CBA, which reads NB05's outputs) and the NB09 uncertainty runner all call it,
so a change here propagates to every city. A city can override any value under
``ac.heat_energy`` in its config (same keys as ``DEFAULTS``); the cooling-activity ramp is
also read from the older ``ac.waste_heat.activation`` block. The choices, their sources and
the alternatives considered are documented in ``docs/ac_heat_energy.md``.

Cooling activity, on the city-mean daily 2 m air temperature T
    a(T) = clip((T - t_on) / (t_full - t_on), 0, 1),  t_on 18 C, t_full 25 C.

Household AC electricity (replaced the NUTS-level kWh-per-user table in 2026-10)
    e(a) = e_onset + (e_full - e_onset) a  kWh per AC household on a day with a > 0, else 0;
    e_onset 2.5, e_full 15. Annual kWh per AC user (person) is the sum of e(a) over the
    year's days divided by the persons per household (2.3).
    Zone 0, the cells of the modelled area outside every named district, has AC users but no
    zonal electricity row from Notebook 05; the costing gives it the city mean (2026-10).

Waste-heat warming (replaced the Salamanca et al. 2014 lookup in 2026-10)
    Heat rejected outdoors = electricity x (1 + COP), COP 3.5, all of it sensible. On a day,
    the mean AC heat flux over the built-up area is
        Q = users / persons_per_household x e(a) x (1 + COP) x 1000 / 24 / A_built   [W m-2]
    and the city-wide daily-mean warming is dT = S x Q, with S = 0.02 K per W m-2.
    It is written as dT = dT_full x w(a): dT_full is the warming at full activity (e = e_full)
    and w(a) = e(a) / e_full is the day's consumption weight. The waste-heat penalty is then
    dT_full x sum over days of w(a) x (marginal deaths per degree on that day).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import numpy as np

# Every number below is a modelling choice; docs/ac_heat_energy.md gives the source of each.
DEFAULTS: dict[str, Any] = {
    # "flux" (default) or "lut" (the Salamanca et al. 2014 penetration lookup, kept for
    # sensitivity runs only; its parameters stay in ac.waste_heat of the city config).
    "method": "flux",
    "activation": {
        "t_on_c": 18.0,    # cooling starts (Kou et al. ramp)
        "t_full_c": 25.0,  # cooling at full activity
    },
    "electricity": {
        "kwh_per_household_day_onset": 2.5,   # first cooling day
        "kwh_per_household_day_full": 15.0,   # day at full activity
        "kwh_per_household_day_full_range": [10.0, 20.0],  # NB09 sampling range
        "persons_per_household": 2.3,         # EU-27 mean household size
    },
    "waste_heat": {
        "cop": 3.5,                               # installed split-unit stock (seasonal)
        "sensible_fraction": 1.0,                 # split units reject heat as sensible heat
        "sensitivity_k_per_wm2": 0.02,            # city-wide daily-mean K per W m-2
        "sensitivity_range_k_per_wm2": [0.01, 0.035],  # NB09 sampling range
        "built_lcz_classes": list(range(1, 11)),  # LCZ 1-10: built types
    },
}


def _deep_update(base: dict, over: Mapping) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, Mapping) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def params(cfg: Mapping | None) -> dict:
    """Defaults merged with the city's overrides (``ac.heat_energy`` and the ramp in
    ``ac.waste_heat.activation``); ``ac.waste_heat.method`` selects flux or lut."""
    p = copy.deepcopy(DEFAULTS)
    ac = (cfg or {}).get("ac", {}) or {}
    wh = ac.get("waste_heat", {}) or {}
    act = wh.get("activation", {}) or {}
    for k in ("t_on_c", "t_full_c"):
        if k in act:
            p["activation"][k] = float(act[k])
    if "method" in wh:
        p["method"] = str(wh["method"]).lower()
    _deep_update(p, ac.get("heat_energy", {}) or {})
    return p


def activity(temp_c: np.ndarray, p: Mapping) -> np.ndarray:
    """Cooling activity in [0, 1] for each city-mean daily temperature; 0 where missing."""
    t = np.asarray(temp_c, dtype=float)
    t_on, t_full = float(p["activation"]["t_on_c"]), float(p["activation"]["t_full_c"])
    out = np.zeros(t.shape, dtype=float)
    ok = np.isfinite(t)
    if t_full <= t_on:
        out[ok] = (t[ok] > t_on).astype(float)
    else:
        out[ok] = np.clip((t[ok] - t_on) / (t_full - t_on), 0.0, 1.0)
    return out


def household_kwh(a: np.ndarray, p: Mapping, e_full: float | None = None) -> np.ndarray:
    """AC electricity per AC household on each day (kWh), from its cooling activity."""
    a = np.asarray(a, dtype=float)
    e_on = float(p["electricity"]["kwh_per_household_day_onset"])
    e_max = float(p["electricity"]["kwh_per_household_day_full"] if e_full is None else e_full)
    return np.where(a > 0, e_on + (e_max - e_on) * a, 0.0)


def consumption_weight(temp_c: np.ndarray, p: Mapping, e_full: float | None = None) -> np.ndarray:
    """Each day's AC electricity relative to a day at full activity, w = e(a) / e_full."""
    e_max = float(p["electricity"]["kwh_per_household_day_full"] if e_full is None else e_full)
    return household_kwh(activity(temp_c, p), p, e_max) / e_max


def annual_kwh_per_user(temp_c: np.ndarray, p: Mapping, e_full: float | None = None) -> float:
    """AC electricity per AC user (person) over the days given, normally one year (kWh)."""
    hh = float(p["electricity"]["persons_per_household"])
    return float(np.sum(household_kwh(activity(temp_c, p), p, e_full)) / hh)


def fill_unlisted_zone_kwh(cov, kwh, *, users: str, kwh_col: str, zone: str = "muni_id", year: str = "year"):
    """Add kWh-per-user rows for zones that have AC users but no electricity row of their own.

    Notebook 05 disperses electricity over the named districts only, so zone 0 (the cells of the
    modelled area outside every named district) has current and policy AC users but no kWh row.
    Each such zone gets, per year, the mean kWh per AC user of the zones that have a row,
    weighted by their current AC users (``users`` in ``cov``): the electricity rule's city value.
    ``cov`` and ``kwh`` hold one row per (year, zone); returns ``kwh`` with the added rows.
    """
    import pandas as pd

    listed = set(kwh[zone].astype(int))
    missing = cov.loc[~cov[zone].astype(int).isin(listed), [year, zone]].drop_duplicates()
    if missing.empty:
        return kwh
    have = cov.loc[cov[zone].astype(int).isin(listed), [year, zone, users]].merge(
        kwh[[year, zone, kwh_col]], on=[year, zone], how="left")
    w = have[users].clip(lower=0.0).fillna(0.0)
    sums = have.assign(_w=w, _wv=w * have[kwh_col].fillna(0.0)).groupby(year)[["_w", "_wv"]].sum()
    unweighted = have.groupby(year)[kwh_col].mean()  # a year with no current AC users anywhere
    city_mean = (sums["_wv"] / sums["_w"]).where(sums["_w"] > 0, unweighted).fillna(0.0)
    added = missing.assign(**{kwh_col: missing[year].map(city_mean).fillna(0.0).to_numpy(float)})
    return pd.concat([kwh, added], ignore_index=True)


def built_area_m2(lcz_path: str | Path, p: Mapping) -> float:
    """Built-up area (LCZ classes in ``waste_heat.built_lcz_classes``) of an LCZ raster, m2.

    Handles geographic (degree) and projected rasters; for degrees each row's cell area uses
    the cosine of its latitude.
    """
    import rasterio

    classes = np.asarray(p["waste_heat"]["built_lcz_classes"], dtype=int)
    with rasterio.open(lcz_path) as r:
        a = r.read(1)
        built = np.isin(a, classes)
        dx, dy = abs(r.res[0]), abs(r.res[1])
        if r.crs is not None and r.crs.is_geographic:
            lat = r.bounds.top - (np.arange(a.shape[0]) + 0.5) * dy
            row_area = (dx * 111_320.0 * np.cos(np.radians(lat))) * (dy * 110_574.0)
            return float(np.sum(built.sum(axis=1) * row_area))
        return float(built.sum() * dx * dy)


def warming_full_activity(users: np.ndarray, area_m2: float, p: Mapping,
                          sensitivity: float | None = None, e_full: float | None = None) -> np.ndarray:
    """City-wide daily-mean warming (K) from ``users`` AC users on a day at full activity."""
    wh, el = p["waste_heat"], p["electricity"]
    s = float(wh["sensitivity_k_per_wm2"] if sensitivity is None else sensitivity)
    e_max = float(el["kwh_per_household_day_full"] if e_full is None else e_full)
    if area_m2 <= 0:
        raise ValueError("built-up area must be positive")
    flux = (np.asarray(users, dtype=float) / float(el["persons_per_household"]) * e_max
            * (1.0 + float(wh["cop"])) * float(wh["sensible_fraction"]) * 1000.0 / 24.0 / float(area_m2))
    return s * flux


def describe(p: Mapping, area_m2: float | None = None) -> dict:
    """Flat record of the parameters in use, for the run's metadata files."""
    out = {
        "method": p["method"],
        "t_on_c": p["activation"]["t_on_c"],
        "t_full_c": p["activation"]["t_full_c"],
        "kwh_per_household_day_onset": p["electricity"]["kwh_per_household_day_onset"],
        "kwh_per_household_day_full": p["electricity"]["kwh_per_household_day_full"],
        "kwh_per_household_day_full_range": p["electricity"]["kwh_per_household_day_full_range"],
        "persons_per_household": p["electricity"]["persons_per_household"],
        "cop": p["waste_heat"]["cop"],
        "sensible_fraction": p["waste_heat"]["sensible_fraction"],
        "sensitivity_k_per_wm2": p["waste_heat"]["sensitivity_k_per_wm2"],
        "sensitivity_range_k_per_wm2": p["waste_heat"]["sensitivity_range_k_per_wm2"],
        "built_lcz_classes": p["waste_heat"]["built_lcz_classes"],
        "source": "cityheat/ac_heat_energy.py; docs/ac_heat_energy.md",
    }
    if area_m2 is not None:
        out["built_area_m2"] = float(area_m2)
    return out

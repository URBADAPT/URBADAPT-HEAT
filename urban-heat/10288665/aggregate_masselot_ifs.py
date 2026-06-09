"""
aggregate_masselot_ifs.py
Aggregate Masselot et al. (2023) reconstructed RR curves into URBADAPT age bins
and produce CLIMADA-compatible IF JSON files (one per city).

Input:  reconstructed_rr_curves.csv  (from reconstruct_rr_curves.R)
        results/cityage.csv          (for population weights)
Output: masselot_if_curves_{city}.json  (one per city, same format as Burke IFs)
        masselot_if_comparison.csv      (side-by-side summary for verification)

Age mapping:
  <15   : set to zero (Masselot youngest group is 20-44)
  15-64 : pop-weighted average of 20-44 + 45-64
  65+   : pop-weighted average of 65-74 + 75-84 + 85+
"""

import json
import os
import re
import numpy as np
import pandas as pd
import xarray as xr
from pathlib import Path

# paths 
BASE = Path("/Users/armandeaboudrar-meda/Desktop/CMCC/URBADAPT-HEAT/urban-heat/10288665")
URBAN_HEAT = BASE.parent
RR_CSV = BASE / "reconstructed_rr_curves.csv"
CITYAGE_CSV = BASE / "results" / "cityage.csv"
TMEAN_DIST_CSV = BASE / "tmean_distribution.csv"
OUT_DIR = BASE  # output alongside source data
os.environ.setdefault("MPLCONFIGDIR", str(URBAN_HEAT / "outputs" / ".mpl"))

# constants
TARGET_GRID = np.arange(0, 45.5, 0.5)   # 0 to 45 °C, 0.5 steps
AGE_ORDER = ["<15", "15-64", "65+"]
AGE_TO_ID = {"<15": 1, "15-64": 2, "65+": 3}

CITIES = {
    "Rome":       "IT001C",
    "Athens":     "EL001C",
    "Lisbon":     "PT001C",
    "Copenhagen": "DK001C",
}

SLUGS = {
    "Rome": "rome",
    "Athens": "athens",
    "Lisbon": "lisbon",
    "Copenhagen": "copenhagen",
}

SCALING_CSVS = {
    "Rome": [
        URBAN_HEAT / "outputs" / "rome" / "interim" / "baseline_heat_scaling_rome.csv",
        URBAN_HEAT / "outputs" / "Rome" / "interim" / "baseline_heat_scaling_rome.csv",
    ],
    "Athens": [
        URBAN_HEAT / "outputs" / "athens" / "interim" / "baseline_heat_scaling_athens.csv",
    ],
    "Lisbon": [
        URBAN_HEAT / "outputs" / "lisbon" / "interim" / "baseline_heat_scaling_lisbon.csv",
    ],
    "Copenhagen": [
        URBAN_HEAT / "outputs" / "copenhagen" / "interim" / "baseline_heat_scaling_copenhagen.csv",
    ],
}

INTERIM_OUTPUTS = {
    "Rome": [
        URBAN_HEAT / "outputs" / "rome" / "interim" / "if_curves_by_year_rome_masselot.json",
        URBAN_HEAT / "outputs" / "Rome" / "interim" / "if_curves_by_year_rome_masselot.json",
        URBAN_HEAT / "outputs" / "rome_lczcgen" / "interim" / "if_curves_by_year_rome_masselot.json",
    ],
    "Athens": [
        URBAN_HEAT / "outputs" / "athens" / "interim" / "if_curves_by_year_athens_masselot.json",
    ],
    "Lisbon": [
        URBAN_HEAT / "outputs" / "lisbon" / "interim" / "if_curves_by_year_lisbon_masselot.json",
    ],
    "Copenhagen": [
        URBAN_HEAT / "outputs" / "copenhagen" / "interim" / "if_curves_by_year_copenhagen_masselot.json",
    ],
}

INTERIM_OUTPUTS_TAIL = {
    "Rome": [
        URBAN_HEAT / "outputs" / "rome" / "interim" / "if_curves_by_year_rome_masselot_tail.json",
        URBAN_HEAT / "outputs" / "Rome" / "interim" / "if_curves_by_year_rome_masselot_tail.json",
        URBAN_HEAT / "outputs" / "rome_lczcgen" / "interim" / "if_curves_by_year_rome_masselot_tail.json",
    ],
    "Athens": [
        URBAN_HEAT / "outputs" / "athens" / "interim" / "if_curves_by_year_athens_masselot_tail.json",
    ],
    "Lisbon": [
        URBAN_HEAT / "outputs" / "lisbon" / "interim" / "if_curves_by_year_lisbon_masselot_tail.json",
    ],
    "Copenhagen": [
        URBAN_HEAT / "outputs" / "copenhagen" / "interim" / "if_curves_by_year_copenhagen_masselot_tail.json",
    ],
}

# Masselot age groups → URBADAPT bins
AGG_MAP = {
    "15-64": ["20-44", "45-64"],
    "65+":   ["65-74", "75-84", "85+"],
}

CHECK_TEMPS = [20, 25, 28, 30, 33, 35, 38, 40]
# Below this MDD value both Burke and Masselot are effectively zero (well below
# any policy-relevant heat-mortality magnitude: 1e-9 MDD == 1e-4 excess deaths
# per 100k-person-day). Ratios computed across rows that close to zero are
# numerical noise — most visibly the Copenhagen power-law cold-tail rows where
# burke_powerlaw_mdd reaches ~1e-11 at T=20C and produced spurious O(10^2-10^3)
# ratios in the QA summary. Rows below this threshold get NaN ratios and are
# ignored by the summary's min/median/max.
MIN_MDD_FOR_RATIO = 1e-9

# Cities whose Burke IF is built in a different coordinate system than the
# Mediterranean default and therefore cannot be compared to Masselot at fixed
# absolute temperatures. Copenhagen uses the extreme/event hazard track with a
# cold-city Track-B Burke anchor profile (anchors at T_ref + 0.2..3.0 deg C,
# T_ref ~= 19.16 deg C from NB03). Its Burke IF is only calibrated over a narrow
# exceedance band; applying it at absolute T=30..40 deg C is methodologically
# meaningless. These cities appear in the detailed CSV with raw MDDs preserved
# but ratio columns NaN, and they are excluded from the summary aggregation.
EVENT_TRACK_CITIES = {"Copenhagen"}
BURKE_FAMILIES = {
    "burke_polynomial": "if_curves_by_year_{slug}.json",
    "burke_powerlaw": "if_curves_by_year_{slug}_powerlaw.json",
    "masselot": "if_curves_by_year_{slug}_masselot.json",
    "masselot_tail": "if_curves_by_year_{slug}_masselot_tail.json",
}
HAZARD_RE = re.compile(
    r"T2M_daily_mean_(?P<year>\d{4})_FUA_degC__bm-(?P<baseline_mode>.+?)"
    r"__sc-(?P<scenario>.+?)__pb-(?P<band>.+?)\.nc$"
)


def load_data():
    rr = pd.read_csv(RR_CSV)
    ca = pd.read_csv(CITYAGE_CSV)
    tdist = pd.read_csv(TMEAN_DIST_CSV)
    return rr, ca, tdist


def first_existing(paths):
    for path in paths:
        if path.exists():
            return path
    return None


def output_dir_candidates(city_label):
    slug = SLUGS[city_label]
    paths = [URBAN_HEAT / "outputs" / slug]
    if city_label == "Rome":
        paths.extend([
            URBAN_HEAT / "outputs" / "Rome",
            URBAN_HEAT / "outputs" / "rome_lczcgen",
        ])
    return paths


def get_pop_weights(ca, urau_code, masselot_groups):
    """Return population weights for given Masselot age groups within a city."""
    sub = ca[(ca["URAU_CODE"] == urau_code) & (ca["agegroup"].isin(masselot_groups))]
    pops = sub.set_index("agegroup")["agepop"].to_dict()
    total = sum(pops[g] for g in masselot_groups)
    weights = {g: pops[g] / total for g in masselot_groups}
    return weights


def load_baseline_scaling(city_label):
    """Load existing NB04/Wittgenstein relative mortality scaling by URBADAPT age bin."""
    path = first_existing(SCALING_CSVS[city_label])
    if path is None or not path.exists():
        rows = [{"year": yr, **{age: 1.0 for age in AGE_ORDER}} for yr in YEARS]
        return pd.DataFrame(rows)
    scaling = pd.read_csv(path)
    missing = sorted(set(["year", *AGE_ORDER]) - set(scaling.columns))
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    return scaling[["year", *AGE_ORDER]].copy()


def interpolate_constant_extrap(temps, values, target_grid):
    """
    Interpolate values onto target_grid.
    Beyond the source data range, hold constant at the boundary value.
    This is conservative: avoids unreliable extrapolation beyond observed temperatures.
    """
    t_min, t_max = temps.min(), temps.max()
    result = np.zeros(len(target_grid))

    # Interpolate within range
    mask_in = (target_grid >= t_min) & (target_grid <= t_max)
    if mask_in.any():
        result[mask_in] = np.interp(target_grid[mask_in], temps, values)

    # Beyond right boundary: hold constant at last value
    mask_right = target_grid > t_max
    result[mask_right] = values[-1]

    # Beyond left boundary: hold constant at first value
    mask_left = target_grid < t_min
    result[mask_left] = values[0]

    return result


def interpolate_loglinear_tail(rr_sub, ca_df, urau_code, target_grid):
    """
    Interpolate MDD onto target_grid with log-RR linear tail extrapolation
    beyond the Masselot historical tmax.

    For T > tmax:
      log_rr(T) = log_rr(tmax) + slope * (T - tmax)
      RR(T)     = exp(log_rr(T))
      AF(T)     = max(0, (RR - 1) / RR)
      MDD(T)    = baseline_daily_per_person * AF(T)
    """
    temps = rr_sub["temperature"].values
    mdd_src = rr_sub["mdd_heat_per_person_day"].values
    log_rr_src = rr_sub["log_rr"].values
    mmt = float(rr_sub["mmt"].iloc[0])
    tmax = float(rr_sub["tmax"].iloc[0])
    slope = float(rr_sub["log_rr_slope_at_tmax"].iloc[0])
    baseline_daily = float(rr_sub["baseline_daily_per_person"].iloc[0])

    t_min = temps.min()
    result = np.zeros(len(target_grid))

    # Within observed range: use existing MDD (same as constant extrap)
    mask_in = (target_grid >= t_min) & (target_grid <= tmax)
    if mask_in.any():
        result[mask_in] = np.interp(target_grid[mask_in], temps, mdd_src)

    # Below support: hold constant at first value
    mask_left = target_grid < t_min
    result[mask_left] = mdd_src[0]

    # Above tmax: extend log-RR linearly, convert through AF
    mask_right = target_grid > tmax
    if mask_right.any() and slope > 0:
        log_rr_at_tmax = float(np.interp(tmax, temps, log_rr_src))
        delta_T = target_grid[mask_right] - tmax
        log_rr_ext = log_rr_at_tmax + slope * delta_T
        rr_ext = np.exp(log_rr_ext)
        af_ext = np.clip((rr_ext - 1.0) / rr_ext, 0.0, None)
        # Zero out below MMT (should not happen above tmax, but be safe)
        af_ext[target_grid[mask_right] < mmt] = 0.0
        result[mask_right] = baseline_daily * af_ext
    elif mask_right.any():
        # slope == 0: fall back to constant (same as conservative)
        result[mask_right] = mdd_src[-1]

    return np.clip(result, 0.0, 1.0)


def aggregate_excess_deaths(rr_df, ca_df, city_label, urau_code, tail_mode="constant"):
    """
    For one city, aggregate Masselot's 5 age groups into URBADAPT's 3 bins.
    Returns dict: age_label -> (target_grid, mdd_array)

    tail_mode: "constant" (hold MDD flat) or "loglinear" (extend log-RR tail)
    """
    city_rr = rr_df[rr_df["URAU_CODE"] == urau_code].copy()
    results = {}

    # <15: set to zero 
    results["<15"] = np.zeros(len(TARGET_GRID))

    # 15-64 and 65+ : population-weighted per-person daily mdd 
    for urbadapt_bin, masselot_groups in AGG_MAP.items():
        weights = get_pop_weights(ca_df, urau_code, masselot_groups)

        mdd_agg = np.zeros(len(TARGET_GRID))

        for mg in masselot_groups:
            sub = city_rr[city_rr["agegroup"] == mg].sort_values("temperature")
            temps = sub["temperature"].values
            if "mdd_heat_per_person_day" not in sub.columns:
                raise ValueError(
                    "reconstructed_rr_curves.csv must contain "
                    "mdd_heat_per_person_day from the AF-based R reconstruction"
                )
            mdd_src = sub["mdd_heat_per_person_day"].values

            if tail_mode == "loglinear" and "log_rr_slope_at_tmax" in sub.columns:
                mdd_interp = interpolate_loglinear_tail(sub, ca_df, urau_code, TARGET_GRID)
            else:
                mdd_interp = interpolate_constant_extrap(temps, mdd_src, TARGET_GRID)

            # Weight by population share within this bin
            mdd_agg += weights[mg] * mdd_interp

        results[urbadapt_bin] = np.clip(mdd_agg, 0.0, 1.0)

    return results


YEARS = [2020, 2030, 2040, 2050]


def interp_mdd(block, age, temp):
    rec = block[age]
    return float(np.interp(float(temp), np.asarray(rec["intensity"], float), np.asarray(rec["mdd"], float)))


def load_family_json(city_label, family):
    slug = SLUGS[city_label]
    fname = BURKE_FAMILIES[family].format(slug=slug)
    candidates = [out / "interim" / fname for out in output_dir_candidates(city_label)]
    path = first_existing(candidates)
    if path is None:
        return None, None
    with open(path, "r") as f:
        return json.load(f), path


def masselot_tmax_by_city(tdist_df):
    out = {}
    for city_label, urau_code in CITIES.items():
        row = tdist_df[tdist_df["URAU_CODE"] == urau_code]
        if row.empty:
            raise ValueError(f"Missing tmean_distribution row for {city_label} ({urau_code})")
        out[city_label] = float(row.iloc[0]["100.0%"])
    return out


def build_city_json(mdd_by_age, city_label, scaling_df, tail_mode="constant"):
    """Build JSON dict matching Burke IF format for all projection years."""
    scaling_by_year = {
        int(row["year"]): {age: float(row[age]) for age in AGE_ORDER}
        for _, row in scaling_df.iterrows()
    }
    ifs_by_year = {}
    for yr in YEARS:
        block = {}
        for age in AGE_ORDER:
            scale = scaling_by_year.get(int(yr), {}).get(age, 1.0)
            block[age] = {
                "id": AGE_TO_ID[age],
                "name": f"{age}_{yr}",
                "intensity": TARGET_GRID.tolist(),
                "mdd": np.clip(mdd_by_age[age] * scale, 0.0, 1.0).tolist(),
            }
        ifs_by_year[str(yr)] = block
    return {
        "reference_year": 2020,
        "years": YEARS,
        "scaling_source": (
            "Masselot et al. (2023) Lancet Planet Health, Zenodo 10288665; "
            "future years use existing NB04 baseline_heat_scaling_{city}.csv ratios"
        ),
        "tail_mode": tail_mode,
        "notes": (
            "Age-differentiated RR curves reconstructed from coefs.csv B-spline + "
            "city/age-specific Masselot MMT. No p99 rescaling and no 20C threshold. "
            "RR curves are converted to heat attributable fractions, AF=(RR-1)/RR, "
            "then to CLIMADA mdd using Masselot city-age annual death rates. "
            "Aggregated to URBADAPT bins via population-weighted per-person mdd. "
            "<15 set to zero; 15-64 approximated with Masselot 20-64. "
            "2030/2040/2050 multiply the 2020 Masselot level by existing NB04 "
            "relative baseline-mortality scaling. "
            + (
                "Constant extrapolation beyond historical T range (conservative). "
                if tail_mode == "constant" else
                "Log-linear tail extrapolation beyond historical tmax: log_rr extended "
                "using nonneg-constrained last-1C slope, converted through AF=(RR-1)/RR. "
            )
        ),
        "ifs_by_year": ifs_by_year,
    }


def write_mdd_qa(rr_df, ca_df, city_jsons_by_mode):
    rows = []
    for city_label, urau_code in CITIES.items():
        city_rr = rr_df[rr_df["URAU_CODE"] == urau_code].copy()

        for ag, sub in city_rr.groupby("agegroup"):
            sub = sub.sort_values("temperature")
            mmt = float(sub["mmt"].iloc[0])
            mdd = sub["mdd_heat_per_person_day"].to_numpy(float)
            temps = sub["temperature"].to_numpy(float)
            below = temps < (mmt - 1e-9)
            rows.append({
                "source": "reconstructed_city_age",
                "tail_mode": "source_curve",
                "city": city_label,
                "year": 2020,
                "age_group": ag,
                "mmt_min": mmt,
                "mmt_max": mmt,
                "n_points": len(mdd),
                "finite": bool(np.isfinite(mdd).all()),
                "nonnegative": bool(np.nanmin(mdd) >= -1e-15),
                "bounded_le_1": bool(np.nanmax(mdd) <= 1.0 + 1e-15),
                "zero_below_mmt_min": bool(np.nanmax(np.abs(mdd[below])) <= 1e-15) if below.any() else True,
                "max_abs_mdd_below_mmt_min": float(np.nanmax(np.abs(mdd[below]))) if below.any() else 0.0,
                "mdd_min": float(np.nanmin(mdd)),
                "mdd_max": float(np.nanmax(mdd)),
                **{f"mdd_at_{t}C": float(np.interp(t, temps, mdd)) for t in CHECK_TEMPS},
            })

        for tail_mode, city_jsons in city_jsons_by_mode.items():
            if city_label not in city_jsons:
                continue
            for year in YEARS:
                block = city_jsons[city_label]["ifs_by_year"][str(year)]
                for age in AGE_ORDER:
                    mdd = np.asarray(block[age]["mdd"], float)
                    temps = np.asarray(block[age]["intensity"], float)
                    if age == "<15":
                        mmt_min = np.nan
                        mmt_max = np.nan
                        below = np.ones_like(temps, dtype=bool)
                    else:
                        component_mmts = []
                        for mg in AGG_MAP[age]:
                            ca_row = ca_df[(ca_df["URAU_CODE"] == urau_code) & (ca_df["agegroup"] == mg)]
                            component_mmts.append(float(ca_row.iloc[0]["mmt"]))
                        mmt_min = min(component_mmts)
                        mmt_max = max(component_mmts)
                        below = temps < (mmt_min - 1e-9)
                    rows.append({
                        "source": "urbadapt_json",
                        "tail_mode": tail_mode,
                        "city": city_label,
                        "year": year,
                        "age_group": age,
                        "mmt_min": mmt_min,
                        "mmt_max": mmt_max,
                        "n_points": len(mdd),
                        "finite": bool(np.isfinite(mdd).all()),
                        "nonnegative": bool(np.nanmin(mdd) >= -1e-15),
                        "bounded_le_1": bool(np.nanmax(mdd) <= 1.0 + 1e-15),
                        "zero_below_mmt_min": bool(np.nanmax(np.abs(mdd[below])) <= 1e-15) if below.any() else True,
                        "max_abs_mdd_below_mmt_min": float(np.nanmax(np.abs(mdd[below]))) if below.any() else 0.0,
                        "mdd_min": float(np.nanmin(mdd)),
                        "mdd_max": float(np.nanmax(mdd)),
                        **{f"mdd_at_{t}C": float(np.interp(t, temps, mdd)) for t in CHECK_TEMPS},
                    })

    out = pd.DataFrame(rows)
    out_path = OUT_DIR / "masselot_mdd_qa.csv"
    out.to_csv(out_path, index=False)
    return out_path, out


def write_burke_masselot_comparison(tdist_df):
    tmax_map = masselot_tmax_by_city(tdist_df)
    rows = []
    loaded = {}
    for city_label in CITIES:
        loaded[city_label] = {}
        for family in BURKE_FAMILIES:
            js, path = load_family_json(city_label, family)
            loaded[city_label][family] = js
            if js is None:
                print(f"  !! Missing {family} JSON for {city_label}; skipping in comparison")

        is_event_track = city_label in EVENT_TRACK_CITIES
        coordinate_system = (
            "extreme_exceedance_above_Tstar" if is_event_track else "standard_dailymean_T"
        )
        for year in YEARS:
            for age in AGE_ORDER:
                for temp in CHECK_TEMPS:
                    row = {
                        "city": city_label,
                        "year": year,
                        "age_group": age,
                        "temperature": temp,
                        "coordinate_system": coordinate_system,
                        "masselot_historical_tmax_C": tmax_map[city_label],
                        "above_masselot_historical_tmax": bool(float(temp) > tmax_map[city_label]),
                    }
                    for family in BURKE_FAMILIES:
                        js = loaded[city_label].get(family)
                        if js is None:
                            row[f"{family}_mdd"] = np.nan
                            row[f"{family}_excess_per_100k_day"] = np.nan
                            continue
                        block = js["ifs_by_year"][str(year)] if "ifs_by_year" in js else js
                        mdd = interp_mdd(block, age, temp)
                        row[f"{family}_mdd"] = mdd
                        row[f"{family}_excess_per_100k_day"] = mdd * 100000.0
                    if is_event_track:
                        # Burke IF for event-track cities is calibrated in
                        # exceedance space (T - T*), not absolute T. Ratios at
                        # fixed absolute temperatures are not meaningful for
                        # these cities; raw MDDs above are kept for inspection
                        # but ratio columns are explicitly NaN.
                        for masselot_family in ["masselot", "masselot_tail"]:
                            for family in ["burke_polynomial", "burke_powerlaw"]:
                                row[f"{masselot_family}_to_{family}_ratio"] = np.nan
                        row["masselot_tail_to_masselot_ratio"] = np.nan
                    else:
                        for masselot_family in ["masselot", "masselot_tail"]:
                            masselot = row.get(f"{masselot_family}_mdd", np.nan)
                            for family in ["burke_polynomial", "burke_powerlaw"]:
                                burke = row[f"{family}_mdd"]
                                row[f"{masselot_family}_to_{family}_ratio"] = (
                                    masselot / burke
                                    if np.isfinite(masselot) and np.isfinite(burke)
                                    and masselot > MIN_MDD_FOR_RATIO and burke > MIN_MDD_FOR_RATIO
                                    else np.nan
                                )
                        masselot = row.get("masselot_mdd", np.nan)
                        masselot_tail = row.get("masselot_tail_mdd", np.nan)
                        row["masselot_tail_to_masselot_ratio"] = (
                            masselot_tail / masselot
                            if np.isfinite(masselot_tail) and np.isfinite(masselot)
                            and masselot_tail > MIN_MDD_FOR_RATIO and masselot > MIN_MDD_FOR_RATIO
                            else np.nan
                        )
                    rows.append(row)

    out = pd.DataFrame(rows)
    csv_path = OUT_DIR / "burke_masselot_if_comparison.csv"
    out.to_csv(csv_path, index=False)

    ratio_cols = [
        "masselot_to_burke_polynomial_ratio",
        "masselot_to_burke_powerlaw_ratio",
        "masselot_tail_to_burke_polynomial_ratio",
        "masselot_tail_to_burke_powerlaw_ratio",
        "masselot_tail_to_masselot_ratio",
    ]
    total_cells = int(len(out) * len(ratio_cols))
    nan_cells = int(sum(out[col].isna().sum() for col in ratio_cols))
    event_track_rows = int((out["city"].isin(EVENT_TRACK_CITIES)).sum())
    print(
        f"  burke_masselot_if_comparison: {nan_cells}/{total_cells} ratio cells "
        f"masked as NaN (MIN_MDD_FOR_RATIO={MIN_MDD_FOR_RATIO:g}); "
        f"of these, {event_track_rows * len(ratio_cols)} cells are from "
        f"event-track cities {sorted(EVENT_TRACK_CITIES)} (excluded from summary "
        "because Burke IF is in exceedance space, not absolute T). Summary "
        "stats below ignore all NaN rows."
    )

    summary = (
        out[(out["year"] == 2020) & (~out["city"].isin(EVENT_TRACK_CITIES))]
        .groupby(["city", "age_group"], as_index=False)
        .agg(
            median_ratio_vs_burke_polynomial=("masselot_to_burke_polynomial_ratio", "median"),
            min_ratio_vs_burke_polynomial=("masselot_to_burke_polynomial_ratio", "min"),
            max_ratio_vs_burke_polynomial=("masselot_to_burke_polynomial_ratio", "max"),
            median_ratio_vs_burke_powerlaw=("masselot_to_burke_powerlaw_ratio", "median"),
            min_ratio_vs_burke_powerlaw=("masselot_to_burke_powerlaw_ratio", "min"),
            max_ratio_vs_burke_powerlaw=("masselot_to_burke_powerlaw_ratio", "max"),
            median_tail_ratio_vs_burke_polynomial=("masselot_tail_to_burke_polynomial_ratio", "median"),
            min_tail_ratio_vs_burke_polynomial=("masselot_tail_to_burke_polynomial_ratio", "min"),
            max_tail_ratio_vs_burke_polynomial=("masselot_tail_to_burke_polynomial_ratio", "max"),
            median_tail_ratio_vs_burke_powerlaw=("masselot_tail_to_burke_powerlaw_ratio", "median"),
            min_tail_ratio_vs_burke_powerlaw=("masselot_tail_to_burke_powerlaw_ratio", "min"),
            max_tail_ratio_vs_burke_powerlaw=("masselot_tail_to_burke_powerlaw_ratio", "max"),
            median_tail_to_constant_ratio=("masselot_tail_to_masselot_ratio", "median"),
            min_tail_to_constant_ratio=("masselot_tail_to_masselot_ratio", "min"),
            max_tail_to_constant_ratio=("masselot_tail_to_masselot_ratio", "max"),
        )
    )
    summary_path = OUT_DIR / "burke_masselot_if_comparison_summary.csv"
    summary.to_csv(summary_path, index=False)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        colors = {
            "burke_polynomial": "#1f77b4",
            "burke_powerlaw": "#ff7f0e",
            "masselot": "#2ca02c",
            "masselot_tail": "#9467bd",
        }
        labels = {
            "burke_polynomial": "Burke polynomial",
            "burke_powerlaw": "Burke power-law",
            "masselot": "Masselot",
            "masselot_tail": "Masselot log-linear tail",
        }
        linestyles = {
            "burke_polynomial": "-",
            "burke_powerlaw": "-",
            "masselot": "-",
            "masselot_tail": "--",
        }
        for year in [2020, 2050]:
            fig, axes = plt.subplots(len(CITIES), len(AGE_ORDER), figsize=(13.5, 11), sharex=True)
            for r, city_label in enumerate(CITIES):
                tmax = tmax_map[city_label]
                for c, age in enumerate(AGE_ORDER):
                    ax = axes[r, c]
                    for family in BURKE_FAMILIES:
                        js = loaded[city_label].get(family)
                        if js is None:
                            continue
                        block = js["ifs_by_year"][str(year)] if "ifs_by_year" in js else js
                        rec = block[age]
                        x = np.asarray(rec["intensity"], float)
                        y = np.asarray(rec["mdd"], float) * 100000.0
                        ax.plot(
                            x,
                            y,
                            lw=1.7,
                            color=colors[family],
                            ls=linestyles[family],
                            label=labels[family],
                        )
                    ax.axvline(tmax, color="0.45", lw=1.0, ls=":")
                    ax.set_xlim(15, 42)
                    ax.grid(alpha=0.25)
                    if r == 0:
                        ax.set_title(age)
                    if c == 0:
                        ax.set_ylabel(f"{city_label}\nexcess/100k/day")
                    if r == len(CITIES) - 1:
                        ax.set_xlabel("T2M daily mean (C)")
            handles, plot_labels = axes[0, 0].get_legend_handles_labels()
            fig.legend(handles, plot_labels, loc="upper center", ncol=4, frameon=False)
            fig.suptitle(f"Burke vs Masselot impact functions ({year})", y=0.995)
            fig.tight_layout(rect=[0, 0, 1, 0.965])
            fig.savefig(OUT_DIR / f"burke_masselot_if_curves_{year}.png", dpi=180)
            plt.close(fig)
    except Exception as exc:
        print(f"  !! Plot generation skipped: {type(exc).__name__}: {exc}")

    return csv_path, summary_path, out


def write_tmax_exceedance_diagnostic(tdist_df):
    use_raster_scan = os.environ.get("MASSELOT_TMAX_RASTER_SCAN", "0").strip().lower() in {"1", "true", "yes", "y"}
    if not use_raster_scan:
        tmax_map = masselot_tmax_by_city(tdist_df)
        rows = []
        for city_label in CITIES:
            slug = SLUGS[city_label]
            event_candidates = [
                URBAN_HEAT / "outputs" / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv"
            ]
            if city_label == "Rome":
                event_candidates.extend([
                    URBAN_HEAT / "outputs" / "Rome" / "interim" / "hazard_events_T2M_daily_rome.csv",
                    URBAN_HEAT / "outputs" / "rome_lczcgen" / "interim" / "hazard_events_T2M_daily_rome.csv",
                ])

            event_path = None
            first_event_path = None
            first_event_header = None
            for cand in event_candidates:
                if not cand.exists():
                    continue
                header = pd.read_csv(cand, nrows=0).columns
                first_event_path = first_event_path or cand
                first_event_header = first_event_header if first_event_header is not None else header
                if "mean_intensity_degC" in header:
                    event_path = cand
                    break

            if event_path is None and first_event_path is None:
                rows.append({
                    "city": city_label,
                    "slug": slug,
                    "year": np.nan,
                    "diagnostic_scope": "daily_event_citymean",
                    "status": "missing_daily_event_csv",
                    "file": "; ".join(str(p) for p in event_candidates),
                    "masselot_historical_tmax_C": tmax_map[city_label],
                })
                continue

            if event_path is None:
                ev = pd.read_csv(first_event_path)
                if "date" in ev.columns:
                    ev["date"] = pd.to_datetime(ev["date"], errors="coerce")
                    ev["year"] = ev["date"].dt.year
                    groups = ev.groupby("year", dropna=True)
                else:
                    groups = [(np.nan, ev)]
                for year, sub in groups:
                    rows.append({
                        "city": city_label,
                        "slug": slug,
                        "year": int(year) if np.isfinite(year) else np.nan,
                        "diagnostic_scope": "daily_event_citymean",
                        "status": "missing_mean_intensity_degC",
                        "file": str(first_event_path),
                        "available_columns": ",".join(first_event_header.astype(str)),
                        "masselot_historical_tmax_C": tmax_map[city_label],
                        "n_days": int(len(sub)),
                        "citymean_max_C": np.nan,
                        "citymean_days_above_tmax": np.nan,
                        "citymean_share_days_above_tmax": np.nan,
                        "support_table_pixel_max_C": np.nan,
                        "support_table_warmseason_max_C": np.nan,
                    })
                continue

            ev = pd.read_csv(event_path, parse_dates=["date"])
            ev["year"] = ev["date"].dt.year

            support_path = URBAN_HEAT / "outputs" / slug / "tables" / f"{slug}_sensitivity_temperature_support.csv"
            support = None
            if support_path.exists():
                support = pd.read_csv(support_path).set_index("year")

            tmax = tmax_map[city_label]
            for year, sub in ev.groupby("year"):
                vals = pd.to_numeric(sub["mean_intensity_degC"], errors="coerce")
                above = vals > tmax
                support_row = support.loc[int(year)] if support is not None and int(year) in support.index else None
                rows.append({
                    "city": city_label,
                    "slug": slug,
                    "year": int(year),
                    "diagnostic_scope": "daily_event_citymean",
                    "status": "ok",
                    "file": str(event_path),
                    "masselot_historical_tmax_C": tmax,
                    "n_days": int(vals.notna().sum()),
                    "citymean_max_C": float(vals.max()),
                    "citymean_days_above_tmax": int(above.sum()),
                    "citymean_share_days_above_tmax": float(above.mean()),
                    "support_table_pixel_max_C": (
                        float(support_row["pixel_max"]) if support_row is not None and "pixel_max" in support_row else np.nan
                    ),
                    "support_table_warmseason_max_C": (
                        float(support_row["warmseason_max"]) if support_row is not None and "warmseason_max" in support_row else np.nan
                    ),
                })
        out = pd.DataFrame(rows)
        out_path = OUT_DIR / "masselot_tmax_exceedance_diagnostic.csv"
        out.to_csv(out_path, index=False)
        return out_path, out

    scan_all_modes = os.environ.get("MASSELOT_TMAX_ALL_MODES", "0").strip().lower() in {"1", "true", "yes", "y"}
    allowed_modes = None if scan_all_modes else {"climatology-mean"}
    tmax_map = masselot_tmax_by_city(tdist_df)
    rows = []
    for city_label in CITIES:
        slug = SLUGS[city_label]
        out_dir = URBAN_HEAT / "outputs" / slug
        mask_path = out_dir / "interim" / "city_mask.npz"
        hazard_dir = out_dir / "hazard"
        if not mask_path.exists() or not hazard_dir.exists():
            print(f"  !! Missing hazard inputs for {city_label}; skipping Tmax diagnostic")
            continue
        city_mask = np.load(mask_path)["city_mask"].astype(bool)
        n_city_cells = int(city_mask.sum())
        tmax = tmax_map[city_label]
        for path in sorted(hazard_dir.glob("T2M_daily_mean_*_FUA_degC__bm-*__sc-*__pb-*.nc")):
            match = HAZARD_RE.match(path.name)
            if not match:
                continue
            baseline_mode = match.group("baseline_mode")
            if allowed_modes is not None and baseline_mode not in allowed_modes:
                continue
            print(f"  Tmax scan {city_label}: {path.name}")
            ds = None
            open_errors = []
            for engine in (None, "netcdf4", "h5netcdf", "scipy"):
                try:
                    ds = xr.open_dataset(path, engine=engine) if engine else xr.open_dataset(path)
                    break
                except Exception as exc:
                    open_errors.append(f"{engine or 'auto'}:{type(exc).__name__}")
            if ds is None:
                rows.append({
                    "city": city_label,
                    "slug": slug,
                    "year": int(match.group("year")),
                    "baseline_mode": baseline_mode,
                    "scenario": match.group("scenario"),
                    "band": match.group("band"),
                    "diagnostic_scope": "all_available_modes" if scan_all_modes else "climatology_mean_only",
                    "status": "open_failed",
                    "open_error": "; ".join(open_errors),
                    "file": str(path),
                    "file_size_bytes": int(path.stat().st_size),
                    "masselot_historical_tmax_C": tmax,
                    "n_days": np.nan,
                    "n_city_cells": n_city_cells,
                    "citymean_max_C": np.nan,
                    "citymean_days_above_tmax": np.nan,
                    "citymean_share_days_above_tmax": np.nan,
                    "pixel_max_C": np.nan,
                    "pixel_days_valid": np.nan,
                    "pixel_days_above_tmax": np.nan,
                    "pixel_share_days_above_tmax": np.nan,
                })
                continue
            try:
                vname = "T2M" if "T2M" in ds.data_vars else list(ds.data_vars)[0]
                da = ds[vname].transpose("time", "y", "x")
                n_days = int(da.sizes["time"])
                citymean_above_count = 0
                citymean_max = -np.inf
                pixel_above_count = 0
                pixel_valid_count = 0
                pixel_max = -np.inf

                for start in range(0, n_days, 64):
                    chunk = da.isel(time=slice(start, min(start + 64, n_days))).values.astype(np.float32)
                    masked = chunk[:, city_mask]
                    valid = np.isfinite(masked)
                    citymean = np.nanmean(masked, axis=1)
                    citymean_above_count += int((citymean > tmax).sum())
                    if citymean.size:
                        citymean_max = max(citymean_max, float(np.nanmax(citymean)))
                    pixel_above_count += int((masked > tmax).sum())
                    pixel_valid_count += int(valid.sum())
                    if masked.size:
                        pixel_max = max(pixel_max, float(np.nanmax(masked)))
            finally:
                ds.close()
            rows.append({
                "city": city_label,
                "slug": slug,
                "year": int(match.group("year")),
                "baseline_mode": baseline_mode,
                "scenario": match.group("scenario"),
                "band": match.group("band"),
                "diagnostic_scope": "all_available_modes" if scan_all_modes else "climatology_mean_only",
                "status": "ok",
                "open_error": "",
                "file": str(path),
                "file_size_bytes": int(path.stat().st_size),
                "masselot_historical_tmax_C": tmax,
                "n_days": n_days,
                "n_city_cells": n_city_cells,
                "citymean_max_C": citymean_max,
                "citymean_days_above_tmax": citymean_above_count,
                "citymean_share_days_above_tmax": float(citymean_above_count / n_days) if n_days else np.nan,
                "pixel_max_C": pixel_max,
                "pixel_days_valid": pixel_valid_count,
                "pixel_days_above_tmax": pixel_above_count,
                "pixel_share_days_above_tmax": (
                    float(pixel_above_count / pixel_valid_count) if pixel_valid_count else np.nan
                ),
            })
    out = pd.DataFrame(rows)
    out_path = OUT_DIR / "masselot_tmax_exceedance_diagnostic.csv"
    out.to_csv(out_path, index=False)
    return out_path, out


def main():
    rr_df, ca_df, tdist_df = load_data()

    comparison_rows = []
    city_jsons = {}

    has_tail_columns = {"log_rr_slope_at_tmax", "tmax"}.issubset(rr_df.columns)
    if not has_tail_columns:
        print("\n!! reconstructed_rr_curves.csv missing log_rr_slope_at_tmax/tmax columns.")
        print("   Re-run reconstruct_rr_curves.R to enable masselot_tail.\n")

    city_jsons_tail = {}

    for city_label, urau_code in CITIES.items():
        print(f"\n{'='*60}")
        print(f"  {city_label} ({urau_code})")
        print(f"{'='*60}")

        mdd_by_age = aggregate_excess_deaths(rr_df, ca_df, city_label, urau_code, tail_mode="constant")
        scaling_df = load_baseline_scaling(city_label)

        # print summary
        for age in AGE_ORDER:
            mdd = mdd_by_age[age]
            for T_check in [25, 28, 30, 33, 35, 38, 40]:
                idx = np.argmin(np.abs(TARGET_GRID - T_check))
                val = mdd[idx] * 100_000  # back to per 100k for readability
                comparison_rows.append({
                    "city": city_label,
                    "age_group": age,
                    "temperature": T_check,
                    "excess_per_100k_day": round(val, 4),
                    "mdd": round(mdd[idx], 10),
                })
            idx30 = np.argmin(np.abs(TARGET_GRID - 30))
            idx35 = np.argmin(np.abs(TARGET_GRID - 35))
            print(f"  {age:>5s}: mdd@30°C = {mdd[idx30]:.2e}, "
                  f"mdd@35°C = {mdd[idx35]:.2e}, "
                  f"excess/100k@30°C = {mdd[idx30]*1e5:.2f}, "
                  f"excess/100k@35°C = {mdd[idx35]*1e5:.2f}")

        # save constant-extrapolation JSON
        js = build_city_json(mdd_by_age, city_label, scaling_df, tail_mode="constant")
        city_jsons[city_label] = js
        slug = SLUGS[city_label]
        out_path = OUT_DIR / f"masselot_if_curves_{slug}.json"
        with open(out_path, "w") as f:
            json.dump(js, f, indent=2)
        print(f"  -> Saved {out_path.name} (constant tail)")
        for interim_path in INTERIM_OUTPUTS.get(city_label, []):
            if interim_path.parent.exists():
                with open(interim_path, "w") as f:
                    json.dump(js, f, indent=2)
                print(f"  -> Updated {interim_path}")

        # save log-linear tail JSON
        if has_tail_columns:
            mdd_by_age_tail = aggregate_excess_deaths(rr_df, ca_df, city_label, urau_code, tail_mode="loglinear")
            js_tail = build_city_json(mdd_by_age_tail, city_label, scaling_df, tail_mode="loglinear")
            city_jsons_tail[city_label] = js_tail
            out_path_tail = OUT_DIR / f"masselot_if_curves_{slug}_tail.json"
            with open(out_path_tail, "w") as f:
                json.dump(js_tail, f, indent=2)
            print(f"  -> Saved {out_path_tail.name} (loglinear tail)")
            for interim_path in INTERIM_OUTPUTS_TAIL.get(city_label, []):
                if interim_path.parent.exists():
                    with open(interim_path, "w") as f:
                        json.dump(js_tail, f, indent=2)
                    print(f"  -> Updated {interim_path}")

            # Print tail comparison at high T
            for age in ["15-64", "65+"]:
                c35 = mdd_by_age[age][np.argmin(np.abs(TARGET_GRID - 35))]
                t35 = mdd_by_age_tail[age][np.argmin(np.abs(TARGET_GRID - 35))]
                c40 = mdd_by_age[age][np.argmin(np.abs(TARGET_GRID - 40))]
                t40 = mdd_by_age_tail[age][np.argmin(np.abs(TARGET_GRID - 40))]
                print(f"    {age} tail vs const: @35C {t35/c35:.2f}x, @40C {t40/c40:.2f}x" if c35 > 0 and c40 > 0 else f"    {age} tail: no data")

    # save comparison CSV
    comp_df = pd.DataFrame(comparison_rows)
    comp_path = OUT_DIR / "masselot_if_comparison.csv"
    comp_df.to_csv(comp_path, index=False)
    print(f"\nSaved comparison: {comp_path}")

    qa_inputs = {"constant": city_jsons}
    if city_jsons_tail:
        qa_inputs["loglinear"] = city_jsons_tail
    qa_path, qa_df = write_mdd_qa(rr_df, ca_df, qa_inputs)
    print(f"Saved mdd QA: {qa_path} ({len(qa_df)} rows)")

    bm_path, bm_summary_path, bm_df = write_burke_masselot_comparison(tdist_df)
    print(f"Saved Burke-vs-Masselot comparison: {bm_path} ({len(bm_df)} rows)")
    print(f"Saved Burke-vs-Masselot summary: {bm_summary_path}")

    tmax_path, tmax_df = write_tmax_exceedance_diagnostic(tdist_df)
    print(f"Saved Tmax exceedance diagnostic: {tmax_path} ({len(tmax_df)} rows)")


if __name__ == "__main__":
    main()

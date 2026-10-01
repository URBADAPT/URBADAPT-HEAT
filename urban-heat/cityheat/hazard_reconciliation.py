"""Reconcile the hazard with the support the exposure-response functions were fitted on.

The impact-function reconstruction is exact: evaluated over Masselot's own 30-year
ERA5-Land daily series it reproduces their published per-city, per-age excess_heat_est to
~1e-12 (`10288665/reconstruction_validation.csv`). Everything that separates our modelled
baseline from theirs therefore sits in the interface between the hazard and the curve, and
it is two distinct faults pulling in opposite directions:

  1. DISCARDED VARIANCE (under-predicts).
     The production hazard is a day-of-year climatological mean, so the burden is computed
     as f(mean of the years) when an average year's burden is mean of f(year). Response
     curves are convex, so the first is strictly smaller, and the gap grows with
     interannual variance. In Dublin the climatological peak falls *below* the
     minimum-mortality temperature and the burden collapses to zero.
     Fix: `variance_corrected_curve` - convolve the curve with the observed daily anomaly
     distribution, which is algebraically the same thing as averaging over years.

  2. REFERENCE-DISTRIBUTION MISMATCH (over-predicts).
     The curves are defined with respect to ERA5-Land city temperatures. UrbClim resolves
     the urban heat island at ~100 m and runs warmer through the bulk of the distribution
     (p99 gap +2.3 C in Milan and Rome, ~0 in Dublin and Nantes), so feeding it to an
     ERA5-fitted curve assigns more heat to the same mortality. Measured against the
     over-prediction that survives fix 1, that gap gives Spearman +0.93 across seven
     cities.
     Fix: `era5_reference_shift` - quantile-map the city reference onto the ERA5-Land
     distribution.

Together these accounted for essentially the whole discrepancy on the cities tested.
Applying only one makes agreement worse, because they were partly cancelling.

Two design choices worth knowing, both deliberate:

  * The variance correction uses JUNE-AUGUST anomalies and produces a SINGLE curve.
    CLIMADA attaches impact functions to exposure points rather than to hazard events, so
    a month-varying curve is not expressible. Restricting the anomaly sample to the warm
    season is not a compromise but an improvement: it lands within 1.5% of the exact
    per-year ensemble on median (9.5% worst, seven cities) against 2.0% for twelve
    month-specific curves and 6.1% for an annually pooled one. Mis-convolving winter days
    costs nothing because the response there is ~0.

  * The ERA5 correction shifts the CITY REFERENCE and leaves each pixel's anomaly about it
    untouched. Quantile-mapping pixel by pixel would compress the intra-urban gradient and
    silently weaken the within-city equity results, which depend on that differentiation.
    Correcting the reference puts the exposure metric on the estimation footing while
    preserving the downscaling.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

JJA = (6, 7, 8)


# --------------------------------------------------------------------------------------
# Fault 1: discarded variance
# --------------------------------------------------------------------------------------

def daily_anomalies(
    daily_by_year: pd.DataFrame, months: tuple[int, ...] = JJA
) -> np.ndarray:
    """Pooled daily anomalies about the day-of-year mean, restricted to `months`.

    `daily_by_year` is a (365, n_years) frame of city-level daily means, one column per
    observed year - the same object the climatology is built from.
    """
    if len(daily_by_year) != 365:
        raise ValueError(f"expected a 365-day index, got {len(daily_by_year)}")
    clim = daily_by_year.mean(axis=1).to_numpy()
    anom = daily_by_year.to_numpy() - clim[:, None]
    doy_month = pd.date_range("2001-01-01", periods=365, freq="D").month.to_numpy()
    return anom[np.isin(doy_month, months)].ravel()


def variance_corrected_curve(
    intensity: np.ndarray, mdd: np.ndarray, anomalies: np.ndarray
) -> np.ndarray:
    """f_tilde(T) = mean over the anomaly sample of f(T + eps).

    Returned on the same `intensity` grid, so it is a drop-in replacement for `mdd`.
    Values beyond the grid are held flat, matching the constant-tail convention of the
    untailed Masselot variant; the log-linear variant carries its own extrapolation in
    the curve it is given, so convolving it preserves that slope.
    """
    intensity = np.asarray(intensity, dtype=float)
    mdd = np.asarray(mdd, dtype=float)
    out = np.zeros_like(intensity)
    for eps in np.asarray(anomalies, dtype=float):
        out += np.interp(intensity + eps, intensity, mdd, left=mdd[0], right=mdd[-1])
    return out / len(anomalies)


# --------------------------------------------------------------------------------------
# Fault 2: reference-distribution mismatch
# --------------------------------------------------------------------------------------

def era5_reference_distribution(
    urau_code: str, tmean_distribution_csv: str | Path
) -> tuple[np.ndarray, np.ndarray]:
    """Masselot's ERA5-Land daily-mean percentile vector for one URAU, sorted by probability."""
    d = pd.read_csv(tmean_distribution_csv)
    row = d.loc[d.URAU_CODE == urau_code]
    if row.empty:
        raise KeyError(f"URAU_CODE {urau_code!r} not in {tmean_distribution_csv}")
    cols = [c for c in d.columns if c.endswith("%")]
    probs = np.array([float(c[:-1]) / 100.0 for c in cols])
    vals = row[cols].to_numpy(dtype=float).ravel()
    order = np.argsort(probs)
    return probs[order], vals[order]


def era5_reference_shift(
    city_reference: np.ndarray,
    historical_reference: np.ndarray,
    era5_probs: np.ndarray,
    era5_values: np.ndarray,
) -> np.ndarray:
    """Per-day additive shift carrying the city reference onto the ERA5-Land distribution.

    `city_reference` is the city-level daily series to correct (the baseline calendar).
    `historical_reference` is the pooled observed city-level series that defines the
    empirical CDF being mapped from - normally every UrbClim day across all years, so the
    mapping is estimated on the full distribution rather than on the smoothed calendar.

    Returns an array the same length as `city_reference`. Add it to every pixel on that
    day: the city reference lands on the ERA5-Land quantile while each pixel keeps its
    anomaly about it.
    """
    hist = np.sort(np.asarray(historical_reference, dtype=float).ravel())
    city = np.asarray(city_reference, dtype=float)
    # Empirical CDF position of each day within the observed record, then read the
    # matching ERA5-Land quantile.
    pos = np.searchsorted(hist, city, side="left") / max(len(hist) - 1, 1)
    pos = np.clip(pos, 0.0, 1.0)
    mapped = np.interp(pos, era5_probs, era5_values)
    return mapped - city

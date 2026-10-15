"""Write variance-corrected Masselot impact-function JSONs for one or more cities.

Addresses fault 1 of two in the hazard-to-curve interface (see
`cityheat/hazard_reconciliation.py` for both). The production hazard is a day-of-year
climatological mean, so the burden is computed as f(mean of the years) when an average
year's burden is mean of f(year); convex curves make the first strictly smaller. Rather
than carry every observed year through the hazard - which multiplies hazard size and
impact computation by ten and touches NB02, NB03, NB04 and every downstream assumption
that a target year has 365 days - the correction is folded into the curve, where it is
algebraically the same operation and costs nothing at runtime.

Reads   10288665/masselot_if_curves_<slug>.json
        10288665/masselot_if_curves_<slug>_tail.json
Writes  10288665/masselot_if_curves_<slug>_varcorr.json
        10288665/masselot_if_curves_<slug>_tail_varcorr.json

The originals are never modified, so the uncorrected families stay reproducible and the
two can be run head to head. Register the new files as impact-function families in
`cityheat/nb04_masselot_main.py` (MASSELOT_SOURCE_FILE) to make NB04 pick them up.

Validation on the seven cities with local UrbClim (dublin, nantes, stockholm, berlin,
milan, rome, sevilla): the corrected curves reproduce the exact per-year ensemble burden
to within 1.5% on median and 9.5% at worst, against an uncorrected default that is 65%
low on median and 100% low in Dublin.

Requires UrbClim daily-mean T2M under data/<City>/UrbClim/UrbClimT2Mmean/. NB01 already
downloads these; cities without them are skipped with a warning rather than guessed at.

Usage:
    python scripts/build_variance_corrected_ifs.py --cities milan
    python scripts/build_variance_corrected_ifs.py            # every city with UrbClim
    python scripts/build_variance_corrected_ifs.py --cities milan --dry-run

Requires: xarray + netCDF4 (the `urbanheat` env), numpy, pandas.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cityheat.hazard_reconciliation import (  # noqa: E402
    daily_anomalies,
    variance_corrected_curve,
)

warnings.filterwarnings("ignore")
MASSELOT_DIR = ROOT / "10288665"
VARIANTS = {"": "_varcorr", "_tail": "_tail_varcorr"}
# Below this many complete UrbClim years the pooled JJA anomaly sample is too thin
# to convolve with; fail loudly rather than emit a quietly undersampled curve.
MIN_YEARS = 5


def city_dirs() -> dict[str, str]:
    """slug -> on-disk city directory, for cities with UrbClim T2M present."""
    out = {}
    for d in sorted((ROOT / "data").glob("*")):
        if d.is_dir() and list(d.glob("UrbClim/UrbClimT2Mmean/T2M_year_daily_mean_*.nc")):
            out[d.name.lower()] = d.name
    return out


def daily_by_year(city_dir: str) -> pd.DataFrame:
    """(365, n_years) city-level daily-mean T2M in degC, one column per observed year."""
    import xarray as xr

    files = sorted(
        glob.glob(str(ROOT / "data" / city_dir
                      / "UrbClim/UrbClimT2Mmean/T2M_year_daily_mean_*.nc"))
    )
    cols, short = {}, []
    for f in files:
        m = re.search(r"(\d{4})\.nc$", f)
        if not m:
            continue
        year = int(m.group(1))
        with xr.open_dataset(f) as ds:
            v = ds.T2M.mean(dim=("x", "y")).values - 273.15
        # Years must share a 365-day index. A leap year is simply truncated, but an
        # UrbClim file can also be SHORT (Athens has one), and silently keeping it
        # used to blow up in the DataFrame constructor with "All arrays must be of
        # the same length" - an error that says nothing about the real cause.
        if len(v) < 365:
            short.append((year, len(v)))
            continue
        cols[year] = v[:365]
    if short:
        print(f"  [warn] dropping short UrbClim years (need 365 days): "
              + ", ".join(f"{y}={n}d" for y, n in short))
    if not cols:
        raise FileNotFoundError(f"no usable UrbClim years under data/{city_dir}")
    if len(cols) < MIN_YEARS:
        raise ValueError(
            f"only {len(cols)} usable UrbClim years under data/{city_dir} "
            f"(need >= {MIN_YEARS}); the anomaly distribution would be too thin "
            f"to convolve with. Short years: {short or 'none'}"
        )
    if len(cols) < 8:
        print(f"  [warn] only {len(cols)} usable years; anomaly sample is thinner "
              "than the 10-year basis the correction was validated on")
    return pd.DataFrame(cols)


def correct_json(src: Path, dst: Path, anomalies: np.ndarray, dry: bool) -> str:
    raw = src.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    n_curves = 0
    for year, block in data.get("ifs_by_year", {}).items():
        for age, rec in block.items():
            x = np.asarray(rec["intensity"], dtype=float)
            y = np.asarray(rec["mdd"], dtype=float)
            rec["mdd"] = [float(v) for v in variance_corrected_curve(x, y, anomalies)]
            n_curves += 1
    data["variance_correction"] = {
        "applied": True,
        "method": "convolution with pooled June-August daily anomalies "
                  "about the day-of-year mean",
        "n_anomaly_samples": int(len(anomalies)),
        "anomaly_sd_c": float(np.std(anomalies)),
        "source_file": src.name,
        # NB04 rebuilds this file whenever the source curves change (hash mismatch).
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "rationale": "the production hazard is a day-of-year climatological mean, so the "
                     "burden is f(mean of the years) where an average year's burden is "
                     "mean of f(year); convolving the curve restores the discarded "
                     "variance without changing the hazard",
    }
    if not dry:
        dst.write_text(json.dumps(data, indent=1))
    return f"{dst.name}  ({n_curves} curves)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cities", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    avail = city_dirs()
    slugs = [s for s in (args.cities or avail)]
    missing = [s for s in slugs if s not in avail]
    slugs = [s for s in slugs if s in avail]
    if missing:
        print(f"[warn] no local UrbClim, skipped: {missing}")
    if not slugs:
        print(f"[error] nothing to do; cities with UrbClim: {sorted(avail)}")
        return 2

    for slug in slugs:
        dm = daily_by_year(avail[slug])
        anom = daily_anomalies(dm)
        print(f"[{slug}] {dm.shape[1]} years | {len(anom)} JJA anomalies | "
              f"sd {anom.std():.2f} C")
        for suffix, out_suffix in VARIANTS.items():
            src = MASSELOT_DIR / f"masselot_if_curves_{slug}{suffix}.json"
            if not src.exists():
                print(f"  [skip] {src.name} not found")
                continue
            dst = MASSELOT_DIR / f"masselot_if_curves_{slug}{out_suffix}.json"
            print(f"  {'[dry] ' if args.dry_run else '[write] '}"
                  f"{correct_json(src, dst, anom, args.dry_run)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

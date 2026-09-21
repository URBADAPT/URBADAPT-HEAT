"""Fill calibration/city_summer_tmax.csv `tmax_jja_c` from the run's UrbClim T2M.

`gen_gvi_reduction.py` needs the JJA (Jun-Jul-Aug) mean **daily-MAXIMUM** 2 m air
temperature per city (Falchetta 2026 Fig-5 x-axis). This script computes it, city-masked,
from each city's downloaded UrbClim T2M over the baseline years (climate.urbclim_api.years).

DATA GAP (important): the pipeline currently downloads only the T2M daily-MEAN product
(data/<city>/UrbClim/UrbClimT2Mmean/T2M_year_daily_mean_YYYY.nc). It does NOT download a
T2M daily-MAX product (the max products present are LST and WBGT, not T2M). So:

  * If a T2M daily-MAX product is present (UrbClimT2Mmax/T2M_year_daily_max_YYYY.nc,
    same VITO compressed_daily naming as WBGTmax), this script fills `tmax_jja_c`
    with the correct JJA mean daily-max -> ready for gen_gvi_reduction.py.
  * If only the daily-MEAN is present (current state), it does NOT write `tmax_jja_c`
    (daily-mean is ~5-7 C below daily-max -> would grossly under-state the Fig-5
    reduction). It records the daily-mean in `tmax_jja_dailymean_ref_c` (reference only)
    and sets status=NEEDS_T2MMAX. Fetch the T2M daily-max first (add UrbClimT2Mmax to
    NB01's UrbClim download, mirroring UrbClimT2Mmean), then re-run this.

Usage:  /opt/anaconda3/envs/urbanheat/bin/python calibration/gen_city_summer_tmax.py
        [--variant masselot_main_agnostic]   # where interim/city_mask.npz lives
"""
from __future__ import annotations
import argparse, glob, re, sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

ROOT = Path(__file__).resolve().parent.parent          # urban-heat/
CONFIGS = ROOT / "configs"
CSV = Path(__file__).resolve().parent / "city_summer_tmax.csv"
JJA = (6, 7, 8)


def _year(fp: str) -> int | None:
    m = re.search(r"(\d{4})", Path(fp).stem)
    return int(m.group(1)) if m else None


def _find_city_mask(slug: str, variant: str) -> np.ndarray | None:
    for p in [
        ROOT / "outputs_variants" / variant / slug / "interim" / "city_mask.npz",
        ROOT / "outputs" / slug / "interim" / "city_mask.npz",
        *(Path(d) for d in glob.glob(str(ROOT / "outputs_variants" / "*" / slug / "interim" / "city_mask.npz"))),
    ]:
        if p.exists():
            try:
                return np.load(p)["city_mask"].astype(bool)
            except Exception:
                pass
    return None


def _jja_citymean(nc_files: list[str], mask: np.ndarray | None) -> float | None:
    if not nc_files:
        return None
    with xr.open_mfdataset(sorted(nc_files), combine="by_coords", decode_times=True) as ds:
        var = "T2M" if "T2M" in ds.data_vars else list(ds.data_vars)[0]
        da = ds[var]
        if mask is not None and mask.shape == da.isel(time=0).shape:
            da = da.where(xr.DataArray(mask, dims=("y", "x")))
        citymean = da.mean(dim=("y", "x"), skipna=True).to_numpy().astype(float)
        months = pd.to_datetime(da["time"].values).month
    if np.nanmedian(citymean) > 150:          # Kelvin -> Celsius
        citymean = citymean - 273.15
    jja = citymean[np.isin(months, JJA)]
    jja = jja[np.isfinite(jja)]
    return float(np.mean(jja)) if jja.size else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="masselot_main_agnostic")
    args = ap.parse_args()

    base = pd.read_csv(CSV) if CSV.exists() else pd.DataFrame({"city": []})
    cities = list(base["city"]) if "city" in base and len(base) else \
        sorted(p.stem for p in CONFIGS.glob("*.yml"))

    rows = []
    for slug in cities:
        cfgp = CONFIGS / f"{slug}.yml"
        if not cfgp.exists():
            rows.append({"city": slug, "status": "NO_CONFIG"}); continue
        cfg = yaml.safe_load(open(cfgp)) or {}
        base_dir = ROOT / str(cfg.get("base_dir", f"data/{slug}"))
        years = set(int(y) for y in (cfg.get("climate", {}).get("urbclim_api", {}) or {}).get("years", range(2008, 2018)))
        mask = _find_city_mask(slug, args.variant)

        maxf = [f for f in glob.glob(str(base_dir / "UrbClim" / "UrbClimT2Mmax" / "T2M_year_daily_max_*.nc")) if _year(f) in years]
        meanf = [f for f in glob.glob(str(base_dir / "UrbClim" / "UrbClimT2Mmean" / "T2M_year_daily_mean_*.nc")) if _year(f) in years]

        row = {"city": slug, "mask": "city" if mask is not None else "grid"}
        if maxf:
            row["tmax_jja_c"] = round(_jja_citymean(maxf, mask), 3)
            row["status"] = "OK (daily-max)"
        elif meanf:
            v = _jja_citymean(meanf, mask)
            row["tmax_jja_dailymean_ref_c"] = round(v, 3) if v is not None else None
            row["status"] = "NEEDS_T2MMAX (only daily-mean present; tmax_jja_c NOT filled)"
        else:
            row["status"] = "NO_URBCLIM_T2M"
        rows.append(row)

    out = pd.DataFrame(rows)
    ok = (out.get("status", pd.Series(dtype=str)).astype(str).str.startswith("OK")).sum()
    print(out.to_string(index=False))
    print(f"\n{ok}/{len(out)} cities got a true daily-max tmax_jja_c.")
    if ok < len(out):
        print("!! Cities flagged NEEDS_T2MMAX: fetch the UrbClim T2M daily-MAX product "
              "(add UrbClimT2Mmax to NB01, mirroring UrbClimT2Mmean) then re-run. "
              "daily-mean is a LOWER bound only — do NOT paste it into tmax_jja_c.")
    # merge into city_summer_tmax.csv (preserve existing filled values / notes)
    if "city" in base and len(base):
        merged = base.merge(out, on="city", how="left", suffixes=("", "_new"))
    else:
        merged = out
    merged.to_csv(CSV, index=False)
    print(f"\nWrote -> {CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

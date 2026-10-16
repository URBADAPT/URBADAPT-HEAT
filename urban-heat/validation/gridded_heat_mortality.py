"""Baseline heat-attributable deaths per grid cell, as Notebook 05 computes them, for maps.

NB05 sums deaths over the city: for every day d and age group a,
    deaths_d = sum_cells pop_a * mdd_a(T_cell,d) * clip(1 - eff_a * cov_cell, 0, 1)
with NB04's year-specific curves, the year's age grids, and the current-AC coverage map,
over CITY_MASK (inside the FUA, populated in the baseline year). This script keeps the cell
dimension instead of summing it, for one year, and writes a GeoTIFF on the UrbClim grid
(EPSG:3035) with bands: deaths per year, residents, deaths per 100k residents per year.
The cell sum is checked against NB05's city total (marginal_heat_deaths_perC_currentAC_<city>.csv,
deaths_T), which it must reproduce.

Usage: python gridded_heat_mortality.py <urban-heat repo> <variant> <out dir> <year> <city> [<city> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
import yaml
from affine import Affine
from climada.hazard import Hazard

AGE_KEYS = {"<15": "lt15", "15-64": "a15_64", "65+": "g65"}


def first_existing(paths):
    for p in paths:
        if p.exists():
            return p
    raise FileNotFoundError(", ".join(map(str, paths)))


def resolve(int_dir: Path, recorded: str) -> Path:
    """An NB03 manifest path, resolved after a host move."""
    p = Path(recorded)
    return first_existing([p, int_dir / p.name])


def efficacies(cfg: dict) -> dict:
    """NB05 cell 17: the 'moderate' scenario if configured, else the efficacy block."""
    eff_cfg = cfg.get("efficacy", {}) or {}
    default = float(eff_cfg.get("default", 0.30))
    scen = (cfg.get("efficacy_scenarios", {}) or {}).get("moderate")
    buckets = {k: float(v) for k, v in (scen or eff_cfg).items() if k != "default"}
    for k in AGE_KEYS:
        buckets.setdefault(k, default)
    return buckets


def city_grid(repo: Path, variant: str, slug: str, year: int, out_dir: Path) -> dict:
    out = repo / "outputs_variants" / variant / slug
    int_dir = out / "interim"
    cfg = yaml.safe_load((repo / "configs" / f"{slug}.yml").read_text(encoding="utf-8")) or {}

    # Reference grid: NB05 takes it from a saved T2M hazard file (NaN outside the FUA).
    with xr.open_dataset(first_existing([out / "hazard" / "T2M_daily_mean_2030_FUA_degC.nc",
                                         out / "hazard" / f"T2M_daily_mean_{year}_FUA_degC.nc"])) as ds:
        ref = ds["T2M"].isel(time=0)
        x, y = ref["x"].values, ref["y"].values
        ref_data = ref.values
    hgt, wdt = ref_data.shape

    man = json.loads((int_dir / "exposure_manifest.json").read_text())
    direct = man.get("direct_worldpop", {}) or {}
    base_year = min(map(int, direct)) if direct else year
    pop_base = np.load(resolve(int_dir, direct[str(base_year)]["pop_npz"]))["pop"].astype(float)
    rec = direct.get(str(year)) or ((man.get("scenarios", {}) or {}).get(cfg.get("exp_scenario", "SSP2"), {}) or {}).get(str(year))
    pop_y = np.load(resolve(int_dir, rec["pop_npz"]))["pop"].astype(float)
    ages = np.load(resolve(int_dir, rec["age_npz"]))
    age_grid = {a: np.nan_to_num(ages[k].astype(float), nan=0.0) for a, k in AGE_KEYS.items()}

    city_mask = np.isfinite(ref_data) & (pop_base > 0)          # NB05 CITY_MASK
    m_city = city_mask & np.isfinite(pop_y)                      # deaths_year_by_age m_city

    cov_npz = np.load(first_existing([int_dir / f"ac_coverage_maps_{slug}.npz", out / f"ac_coverage_maps_{slug}.npz"]))
    years_key = "YEARS_AC" if "YEARS_AC" in cov_npz.files else "years"
    cov_years = [int(v) for v in cov_npz[years_key]]
    cov = cov_npz["coverage_base_3d"][cov_years.index(year)].astype(float)
    cov = np.where(m_city & np.isfinite(cov), cov, np.nan)
    if np.isnan(cov[m_city]).any():                              # NB05 gap fill
        w = pop_y[m_city]; fin = np.isfinite(cov[m_city])
        cov = np.where(m_city & np.isnan(cov), float(np.sum(cov[m_city][fin] * w[fin]) / np.sum(w[fin])), cov)

    ifs = json.loads((int_dir / f"if_curves_by_year_{slug}.json").read_text())["ifs_by_year"]
    ifs_y = ifs.get(str(year)) or ifs[min(ifs, key=lambda k: abs(int(k) - year))]
    eff = efficacies(cfg)

    haz = Hazard.from_hdf5(str(first_existing([int_dir / f"hazard_T2M_daily_{slug}.h5", out / f"hazard_T2M_daily_{slug}.h5"])))
    # NB05 cell 13: the hazard's own dates, else the events CSV.
    dates = pd.DatetimeIndex(pd.to_datetime(haz.date)) if len(getattr(haz, "date", [])) == haz.intensity.shape[0] else \
        pd.DatetimeIndex(pd.read_csv(int_dir / f"hazard_T2M_daily_events_{slug}.csv", parse_dates=["date"])["date"])
    idx = np.where(dates.year == year)[0]
    if haz.intensity.shape[1] != hgt * wdt:
        raise ValueError(f"{slug}: hazard has {haz.intensity.shape[1]} cells, grid {hgt}x{wdt}")

    deaths = np.zeros(hgt * wdt)
    mflat = m_city.ravel()
    covf = np.where(mflat, cov.ravel(), 0.0)
    for start in range(0, idx.size, 31):
        block = haz.intensity[idx[start:start + 31]].toarray().astype(float)   # (days, cells), deg C
        ok = np.isfinite(block) & mflat[None, :]
        for age, f in ifs_y.items():
            mdd = np.interp(block, np.asarray(f["intensity"], float), np.asarray(f["mdd"], float),
                            left=f["mdd"][0], right=f["mdd"][-1])
            mult = np.clip(1.0 - eff[age] * covf, 0.0, 1.0)
            deaths += np.where(ok, age_grid[age].ravel()[None, :] * mdd * mult[None, :], 0.0).sum(0)
    deaths = deaths.reshape(hgt, wdt)

    resid = np.where(m_city, sum(age_grid.values()), np.nan)
    rate = np.where(m_city & (resid > 0), deaths / np.where(resid > 0, resid, 1.0) * 1e5, np.nan)
    deaths = np.where(m_city, deaths, np.nan)

    dx, dy = float(abs(np.diff(x).mean())), float(abs(np.diff(y).mean()))
    flip = y[0] < y[-1]                                          # rasters run north to south
    bands = [np.flipud(b) if flip else b for b in (deaths, resid, rate)]
    transform = Affine.translation(float(x.min()) - dx / 2, float(y.max()) + dy / 2) * Affine.scale(dx, -dy)
    out_dir.mkdir(parents=True, exist_ok=True)
    tif = out_dir / f"heat_mortality_grid_{slug}_{year}.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=hgt, width=wdt, count=3, dtype="float32",
                       crs="EPSG:3035", transform=transform, nodata=np.nan, compress="deflate") as dst:
        for i, (b, name) in enumerate(zip(bands, ("deaths_per_year", "residents", "deaths_per_100k_per_year")), 1):
            dst.write(b.astype("float32"), i)
            dst.set_band_description(i, name)

    ref_total = pd.read_csv(int_dir / f"marginal_heat_deaths_perC_currentAC_{slug}.csv").set_index("year").loc[year, "deaths_T"]
    total = float(np.nansum(deaths))
    return dict(city=slug, year=year, deaths_cells=total, deaths_nb05=float(ref_total),
                rel_diff=total / float(ref_total) - 1, residents=float(np.nansum(resid)),
                rate_p05=float(np.nanpercentile(rate, 5)), rate_median=float(np.nanmedian(rate)),
                rate_p95=float(np.nanpercentile(rate, 95)), tif=str(tif))


def main() -> None:
    repo, variant, out_dir, year = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
    rows = []
    for c in sys.argv[5:]:
        try:
            r = city_grid(repo, variant, c, year, out_dir)
            rows.append(r)
            print(f"{c:12s} cells sum {r['deaths_cells']:.2f} vs NB05 {r['deaths_nb05']:.2f} ({100 * r['rel_diff']:+.3f}%); "
                  f"rate per 100k P5/P50/P95 {r['rate_p05']:.1f}/{r['rate_median']:.1f}/{r['rate_p95']:.1f}", flush=True)
        except Exception as e:
            print(f"{c:12s} FAILED: {type(e).__name__}: {e}", flush=True)
    pd.DataFrame(rows).to_csv(out_dir / f"heat_mortality_grid_summary_{year}.csv", index=False)


if __name__ == "__main__":
    main()

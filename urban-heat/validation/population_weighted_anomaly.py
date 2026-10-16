"""Population-weighted temperature anomaly about the city reference, for every city.

NB02 quantile-maps the CITY REFERENCE - the unweighted mean of UrbClim daily T2M over the
full simulation box - onto the ERA5-Land distribution the exposure-response functions were
fitted on, and keeps each cell's anomaly about that reference. Residents live in the warm
core, so their population-weighted anomaly is positive and their exposure sits above the
city-level index of the curves.

For the 2008-2017 climatology NB02 builds, the anomaly is linear in temperature, so its
period mean only needs each cell's time-mean temperature:

    a_pop = sum_i w_i * Tbar_i  -  mean_box(Tbar_i)

with w_i the 2020 population share of populated cells inside the FUA (NB02's CITY_MASK)
and the box mean over every finite UrbClim cell, as NB02's reference is. Reported for the
whole year and for May-September.

Usage:  python population_weighted_anomaly.py <urban-heat repo> <variant> <out.csv> [city ...]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

YEARS = range(2008, 2018)
WARM = (5, 6, 7, 8, 9)


def pop_grid_2020(int_dir: Path, slug: str) -> np.ndarray:
    """The 2020 population grid NB05/NB06 use (direct WorldPop), resolved after a host move."""
    man = json.loads((int_dir / "exposure_manifest.json").read_text())
    rec = (man.get("direct_worldpop") or {}).get("2020") or {}
    cand = [Path(rec["pop_npz"])] if rec.get("pop_npz") else []
    cand += [int_dir / Path(c).name for c in cand] + [int_dir / f"pop_on_ref_{slug}_2020.npz",
                                                      int_dir / f"pop_on_ref_{slug}.npz"]
    for c in cand:
        if c.exists():
            return np.load(c)["pop"].astype(float)
    raise FileNotFoundError(f"{slug}: no 2020 population grid among {cand}")


def city_anomaly(repo: Path, variant: str, slug: str) -> dict:
    out_dir = repo / "outputs_variants" / variant / slug
    # data/ folders are lowercase for most cities but capitalised for some (data/Rome).
    data_dir = next((d for d in (repo / "data").iterdir() if d.name.lower() == slug), repo / "data" / slug)
    files = sorted((data_dir / "UrbClim" / "UrbClimT2Mmean").glob("T2M_year_daily_mean_*.nc"))
    files = [f for f in files if int(re.search(r"(\d{4})\.nc$", f.name).group(1)) in YEARS]
    if not files:
        raise FileNotFoundError(f"{slug}: no UrbClim T2M files")

    # Sum of T and count per cell, whole year and May-September.
    s_all = s_warm = n_all = n_warm = None
    for f in files:
        with xr.open_dataset(f) as ds:
            da = ds["T2M"]
            months_all = pd.to_datetime(da["time"].values).month
            x_raw, y_raw = da["x"].values, da["y"].values
            for t0 in range(0, da.sizes["time"], 31):  # chunked: a whole year in float64 is GBs
                v = da.isel(time=slice(t0, t0 + 31)).values.astype(np.float64)  # (t, y, x), K
                months = months_all[t0:t0 + 31]
                ok = np.isfinite(v)
                w = np.isin(months, WARM)[:, None, None] & ok
                if s_all is None:
                    s_all = np.zeros(v.shape[1:]); s_warm = np.zeros(v.shape[1:])
                    n_all = np.zeros(v.shape[1:]); n_warm = np.zeros(v.shape[1:])
                s_all += np.where(ok, v, 0.0).sum(0); n_all += ok.sum(0)
                s_warm += np.where(w, v, 0.0).sum(0); n_warm += w.sum(0)
    t_all = np.where(n_all > 0, s_all / np.maximum(n_all, 1), np.nan)
    t_warm = np.where(n_warm > 0, s_warm / np.maximum(n_warm, 1), np.nan)

    # FUA mask and grid orientation from the saved 2020 hazard file (NB02 writes it on the
    # same UrbClim x/y, NaN outside the FUA).
    with xr.open_dataset(out_dir / "hazard" / "T2M_daily_mean_2020_FUA_degC.nc") as ds:
        ref = ds["T2M"].isel(time=0)
        if not (np.array_equal(ref["x"].values, x_raw) and np.array_equal(ref["y"].values, y_raw)):
            raise ValueError(f"{slug}: hazard grid differs from the raw UrbClim grid")
        fua = np.isfinite(ref.values)
    pop = np.nan_to_num(pop_grid_2020(out_dir / "interim", slug), nan=0.0)
    if pop.shape != fua.shape:
        raise ValueError(f"{slug}: population grid {pop.shape} vs hazard grid {fua.shape}")
    city = fua & (pop > 0) & np.isfinite(t_all)
    w = pop[city] / pop[city].sum()

    def a_pop(t):
        box = np.nanmean(t)
        return float(np.sum(w * t[city]) - box), float(box - 273.15)

    a_year, box_year = a_pop(t_all)
    a_warm, box_warm = a_pop(t_warm)
    return dict(city=slug, a_pop_year_C=a_year, a_pop_warm_C=a_warm,
                box_mean_year_C=box_year, box_mean_warm_C=box_warm,
                pop_2020=float(pop[city].sum()), n_city_cells=int(city.sum()),
                n_box_cells=int(np.isfinite(t_all).sum()), n_years=len(files))


def main() -> None:
    repo, variant, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    cities = sys.argv[4:] or sorted(p.name for p in (repo / "outputs_variants" / variant).iterdir()
                                    if (p / "hazard").is_dir())
    rows = []
    for c in cities:
        try:
            r = city_anomaly(repo, variant, c)
            rows.append(r)
            print(f"{c:14s} a_pop year {r['a_pop_year_C']:+.2f} C, May-Sep {r['a_pop_warm_C']:+.2f} C", flush=True)
        except Exception as e:  # one bad city must not lose the rest
            print(f"{c:14s} FAILED: {e}", flush=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out} ({len(rows)} cities)")


if __name__ == "__main__":
    main()

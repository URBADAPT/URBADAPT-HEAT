#!/usr/bin/env python3
"""Ingest Athens into the harmonized within-city income labels.

Athens income is distributed as a ~100 m rasterized postal-code layer
(data/boundaries/income_athens_raster.csv), not per official sub-city unit, so it
is absent from income_subcity_harmonized.csv. This script does a
POPULATION-WEIGHTED spatial extraction of those income cells onto the 53 synoikia
neighbourhood polygons (GR_Athens_synoikia_districts.gpkg): each cell is weighted
by its GHS-POP population, so a neighbourhood's income reflects where people
actually live. It then derives the same within-city quantities the emulator uses
- inc_rel_to_city_median (per-city median = 1) and inc_pct_within_city (the
unweighted within-city midpoint percentile rank, matching the other 36 cities) -
and appends Athens to the harmonized labels.

    python athens_ingest.py --config config_urbadapt.yaml

Reads the 36-city income_subcity_harmonized.csv and writes the Athens-inclusive
file at io.income_labels (income_subcity_harmonized_athens.csv). Idempotent: any
existing GR rows are dropped before Athens is re-appended.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def _sample_pop(ghs_pop_path: str, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """GHS-POP population at each (lon, lat), via one windowed read over the bbox.

    Athens is tiny relative to the global raster, so reading a single window and
    indexing into it is far cheaper than 44k per-point samples.
    """
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.transform import rowcol

    with rasterio.open(ghs_pop_path) as src:
        w = from_bounds(lon.min(), lat.min(), lon.max(), lat.max(), src.transform)
        w = Window(int(w.col_off) - 2, int(w.row_off) - 2,
                   int(w.width) + 4, int(w.height) + 4)
        arr = src.read(1, window=w).astype("float64")
        wt = src.window_transform(w)

    rows, cols = rowcol(wt, np.asarray(lon), np.asarray(lat))
    rows = np.asarray(rows); cols = np.asarray(cols)
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    pop = np.zeros(len(lon), dtype="float64")
    pop[ok] = arr[rows[ok], cols[ok]]
    pop[pop < 0] = 0.0   # GHS-POP declares no nodata; 0 == no people
    return pop


def build_athens(cfg: dict) -> pd.DataFrame:
    import geopandas as gpd

    cs = cfg["covariate_sources"]
    bdir = Path(cs["boundaries_dir"])

    # 1) income raster cells (one row per ~100 m cell, harmonized inc_level)
    cells = pd.read_csv(bdir / "income_athens_raster.csv")
    meta = cells.iloc[0]   # the descriptive metadata is constant for Athens

    # 2) population weight per cell from GHS-POP
    cells["__pop"] = _sample_pop(cs["ghs_pop"], cells["lon"].values, cells["lat"].values)

    # 3) synoikia neighbourhood polygons
    nb = gpd.read_file(bdir / "GR_Athens_synoikia_districts.gpkg",
                       layer="GR_Athens_synoikia_districts")
    nb = nb[["boundary_code", "name_en", "geometry"]].to_crs("EPSG:4326")

    pts = gpd.GeoDataFrame(
        cells, geometry=gpd.points_from_xy(cells["lon"], cells["lat"]), crs="EPSG:4326")
    j = gpd.sjoin(pts, nb, predicate="within", how="inner")

    # 4) population-weighted aggregation per neighbourhood
    j["__iw"] = j["inc_level"] * j["__pop"]
    j["__nw"] = j["income_native"] * j["__pop"]
    grp = j.groupby(["boundary_code", "name_en"], sort=False)
    agg = grp.agg(num=("__iw", "sum"), nnum=("__nw", "sum"), den=("__pop", "sum"),
                  inc_simple=("inc_level", "mean"), nat_simple=("income_native", "mean"),
                  n_cells=("inc_level", "size")).reset_index()
    # pop-weighted mean; fall back to a simple mean if a neighbourhood has zero pop
    agg["inc_level"] = np.where(agg["den"] > 0, agg["num"] / agg["den"], agg["inc_simple"])
    agg["income_native"] = np.where(agg["den"] > 0, agg["nnum"] / agg["den"], agg["nat_simple"])

    n_missing = nb["boundary_code"].nunique() - len(agg)
    print(f"  {len(agg)}/{nb['boundary_code'].nunique()} synoikia got income cells "
          f"({n_missing} had none); {int(agg['n_cells'].sum())} cells assigned, "
          f"{int(agg['den'].sum()):,} people weighted")

    # 5) within-city relative quantities (match the harmonized convention)
    med = agg["inc_level"].median()
    agg["inc_rel_to_city_median"] = agg["inc_level"] / med
    order = agg["inc_rel_to_city_median"].rank(method="average")
    agg["inc_pct_within_city"] = (order - 0.5) / len(agg)

    # 6) assemble rows in the harmonized schema
    out = pd.DataFrame({
        "country": "GR", "country_name": "Greece", "city": "Athens",
        "spatial_unit": "synoikia district",
        "subcity_code": agg["boundary_code"].astype(str).str.strip(),
        "subcity_name": agg["name_en"],
        "inc_level": agg["inc_level"],
        "inc_level_unit": meta["inc_level_unit"],
        "income_native": agg["income_native"],
        "currency_native": meta["currency_native"],
        "income_unit": meta["income_unit"],
        "data_year": meta["data_year"], "ref_year": meta["ref_year"],
        "hicp_deflator": meta["hicp_deflator"], "ppp_2023": meta["ppp_2023"],
        "income_concept": meta["income_concept"],
        "income_stat": "population-weighted neighbourhood mean",
        "concept_family": meta["concept_family"],
        "city_median_income_native": agg["income_native"].median(),
        "inc_rel_to_city_median": agg["inc_rel_to_city_median"],
        "inc_pct_within_city": agg["inc_pct_within_city"],
        "code_status": "synoikia_pop_weighted",
        "source": meta["source"],
        "ppp_source": meta["ppp_source"],
        "deflator_source": meta["deflator_source"],
    })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    cfg = yaml.safe_load(open(ap.parse_args().config))

    out_path = Path(cfg["io"]["income_labels"])             # Athens-inclusive target
    base_path = out_path.with_name("income_subcity_harmonized.csv")  # 36-city source
    if not base_path.exists():
        raise FileNotFoundError(f"base labels not found: {base_path}")

    print(f"Building Athens labels from {base_path.parent}/income_athens_raster.csv ...")
    athens = build_athens(cfg)

    base = pd.read_csv(base_path)
    base = base[base["country"] != "GR"]                     # idempotent re-append
    combined = pd.concat([base, athens[base.columns]], ignore_index=True)
    combined.to_csv(out_path, index=False)
    print(f"Wrote {len(combined)} rows ({len(athens)} Athens) -> {out_path}")
    print("Athens inc_rel_to_city_median: median={:.3f} min={:.3f} max={:.3f}".format(
        athens["inc_rel_to_city_median"].median(),
        athens["inc_rel_to_city_median"].min(), athens["inc_rel_to_city_median"].max()))


if __name__ == "__main__":
    main()

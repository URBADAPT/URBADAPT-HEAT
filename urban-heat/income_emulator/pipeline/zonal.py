"""OPTIONAL helper: build the zone-level predictor table from rasters.

In URBADAPT-HEAT the heavy lifting (reproject GHS / GISCO 2021 census grid to a
common reference grid, with SUM resampling for extensive census counts and
NEAREST for categorical GHS classes) already exists in
cityheat/vulnerability_layer.py (reproject_to_ref). Reuse it where possible.

This module provides a light, standalone zonal-statistics path for when you
just have (a) a polygon layer of zones and (b) a folder of aligned rasters, and
want one CSV row per zone ready for the emulator. Requires geopandas + rasterio
+ rasterstats (install locally; not needed to run the model itself).

Design notes
------------
* Work in EPSG:3035 (ETRS89-LAEA) - the native CRS of GHSL R2023A and the
  GISCO 2021 census grid - so areas and zonal sums are correct.
* Extensive variables (population, employed, foreign-born counts): SUM.
* Intensive / categorical (building height, GHS-AGE class, SMOD): MEAN / MODE.
* Always derive per-capita or share features AFTER aggregation, never before.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def zonal_table(zones_path, rasters: dict, id_col: str, city_col: str,
                centre_xy: dict | None = None, crs="EPSG:3035") -> pd.DataFrame:
    """Compute zonal statistics into the emulator's predictor schema.

    Parameters
    ----------
    zones_path : path to a polygon vector file (GPKG/SHP) of sub-city zones.
    rasters : dict mapping a logical name -> dict(path=..., stat="sum"|"mean").
        Expected logical names (all optional): ghs_pop, ghs_built_s, ghs_built_v,
        ghs_built_h, ghs_age, smod, census_t, census_oth, census_emp,
        census_y1564, census_y0014, census_y65, nightlights.
    id_col, city_col : zone id and city id columns in the vector file.
    centre_xy : optional {city_id: (x, y)} CBD coords in `crs` for dist_to_centre.
    """
    import geopandas as gpd
    from rasterstats import zonal_stats

    zones = gpd.read_file(zones_path).to_crs(crs)
    zones["__area_km2"] = zones.geometry.area / 1e6
    out = zones[[id_col, city_col, "__area_km2"]].copy()

    def zs(path, stat):
        return np.array([
            (r[stat] if r[stat] is not None else np.nan)
            for r in zonal_stats(zones, path, stats=[stat], all_touched=False)
        ], float)

    raw = {}
    for name, spec in rasters.items():
        raw[name] = zs(spec["path"], spec.get("stat", "sum"))

    g = lambda k: raw.get(k, np.full(len(zones), np.nan))
    area = out["__area_km2"].values
    pop = g("ghs_pop")

    df = pd.DataFrame({id_col: out[id_col].values, city_col: out[city_col].values})
    df["pop_zone"] = pop
    df["pop_density"] = pop / np.where(area > 0, area, np.nan)
    df["built_fraction"] = g("ghs_built_s") / np.where(area > 0, area * 1e6, np.nan)
    df["volume_per_capita"] = g("ghs_built_v") / np.where(pop > 0, pop, np.nan)
    df["mean_building_height"] = g("ghs_built_h")
    df["built_age_index"] = g("ghs_age")
    df["smod_urban_centre_share"] = g("smod")  # pre-encode share upstream if needed

    t = g("census_t")
    df["foreign_born_share"] = g("census_oth") / np.where(t > 0, t, np.nan)
    y1564 = g("census_y1564")
    df["employment_rate"] = g("census_emp") / np.where(y1564 > 0, y1564, np.nan)
    df["share_under15"] = g("census_y0014") / np.where(t > 0, t, np.nan)
    df["share_65plus"] = g("census_y65") / np.where(t > 0, t, np.nan)
    df["nightlights_pc"] = g("nightlights") / np.where(pop > 0, pop, np.nan)

    if centre_xy:
        cx = zones.geometry.centroid.x.values
        cy = zones.geometry.centroid.y.values
        d = np.full(len(zones), np.nan)
        for i, city in enumerate(out[city_col].values):
            if city in centre_xy:
                ox, oy = centre_xy[city]
                d[i] = np.hypot(cx[i] - ox, cy[i] - oy) / 1000.0
        df["dist_to_centre"] = d

    return df

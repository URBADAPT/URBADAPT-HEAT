#!/usr/bin/env python3
"""Stage 1: build the per-unit covariate table for the income emulator.

Reads the `covariate_sources` block of the config plus a sub-city boundary layer
(city + subcity_code + geometry) and writes one row per sub-city unit with the
predictor columns the emulator expects.

Sources (confirmed 2026-06-22):
  GHS-POP 2020, GHS-BUILT-V 2020, GHS-BUILT-H 2018  (R2023A, EPSG:4326, ~100 m)
  LCZ filtered hard classes (lcz_filter_v3.tif)
  Eurostat 2021 census grid V3 (1 km, EPSG:3035) via the parquet table
  EU health / education facility points
  GHS Urban Centre DB (city centres, for distance-to-centre)

Run locally (needs geopandas, rasterio, rasterstats, pyarrow):
    python covariates.py --config config_urbadapt.yaml

NOTE on big rasters: the GHS/LCZ GeoTIFFs are global and multi-GB. rasterstats
reads only the window under each polygon, so memory stays small, but make sure
the files are fully synced locally (not OneDrive cloud-only placeholders).
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Geospatial deps imported lazily inside functions so --help works without them.

# LCZ class groupings (Demuzere et al. global map: 1-10 urban, 11-17 natural).
LCZ_GROUPS = {
    "lcz_compact_share":    [1, 2, 3],          # compact high/mid/low-rise
    "lcz_open_share":       [4, 5, 6],          # open high/mid/low-rise
    "lcz_industrial_share": [8, 10],            # large low-rise + heavy industry
    "lcz_vegetation_share": [11, 12, 13, 14],   # dense/scattered trees, bush, low plants
}
CENSUS_NODATA = (-8888, -9999)


# ---------------------------------------------------------------------------
def _gdf(path, crs=None):
    import geopandas as gpd
    g = gpd.read_file(path)
    if crs:
        g = g.to_crs(crs)
    return g


def load_boundaries(cs: dict, income_labels: str | None = None):
    """Unified sub-city polygons with normalized city / subcity_code / country.

    Two modes:
      - manifest: concatenate per-country GeoPackages (boundaries_manifest), rename
        each key column to subcity_code, reproject to work_crs, and assign city by
        joining the code to the harmonized income file.
      - single file: read one layer that already has city + subcity_code columns.
    """
    import geopandas as gpd
    import pandas as pd

    manifest = cs.get("boundaries_manifest")
    if not manifest:
        g = _gdf(cs["boundaries"])
        g = g.rename(columns={cs.get("boundaries_city_col", "city"): "city",
                              cs.get("boundaries_code_col", "subcity_code"): "subcity_code"})
        return g[["city", "subcity_code", "geometry"]].dropna(subset=["geometry"])

    work_crs = cs.get("work_crs", "EPSG:3035")
    bdir = Path(cs.get("boundaries_dir", "."))
    parts = []
    for m in manifest:
        g = gpd.read_file(bdir / m["file"], layer=m.get("layer"))
        g = g.rename(columns={m["key_col"]: "subcity_code"})
        g["subcity_code"] = g["subcity_code"].astype(str).str.strip()
        g["country"] = m["country"]
        # Keep the boundary file's own city column (if any) as a fallback so a
        # brand-new, unlabelled city can be carried through for prediction.
        if "city" in g.columns:
            g["__bnd_city"] = g["city"].astype(str).str.strip().where(g["city"].notna())
        else:
            g["__bnd_city"] = np.nan
        parts.append(g[["country", "subcity_code", "__bnd_city", "geometry"]].to_crs(work_crs))
    bnd = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=work_crs)

    # Assign city from the income file (codes are unique within country).
    if income_labels:
        inc = pd.read_csv(income_labels, dtype=str)
        inc["subcity_code"] = inc["subcity_code"].astype(str).str.strip()
        key = inc[["country", "subcity_code", "city"]].drop_duplicates()
        bnd = bnd.merge(key, on=["country", "subcity_code"], how="left")
        # Unlabelled units (not in the income file) fall back to the boundary's own
        # city; they carry no income, so run.py treats them as prediction targets.
        bnd["city"] = bnd["city"].fillna(bnd["__bnd_city"])
    else:
        bnd["city"] = bnd["__bnd_city"].fillna(bnd["country"])
    bnd = bnd.drop(columns="__bnd_city")

    n_nocity = bnd["city"].isna().sum()
    if n_nocity:
        print(f"  note: {n_nocity} boundary unit(s) had no city (no income match and "
              f"no city column); dropped.")
        bnd = bnd[bnd["city"].notna()]
    return bnd[bnd.geometry.notna() & ~bnd.geometry.is_empty].copy()


# ---------------------------------------------------------------------------
def ghs_features(bnd, cs: dict) -> pd.DataFrame:
    """GHS-POP (sum), GHS-BUILT-V (sum), GHS-BUILT-H (mean) per unit, in EPSG:4326."""
    from rasterstats import zonal_stats
    g = bnd.to_crs("EPSG:4326")
    geom = g.geometry.values

    pop = np.array([s["sum"] or 0.0 for s in
                    zonal_stats(geom, cs["ghs_pop"], stats=["sum"], all_touched=False, nodata=-200)])
    vol = np.array([s["sum"] or 0.0 for s in
                    zonal_stats(geom, cs["ghs_built_v"], stats=["sum"], all_touched=False, nodata=-200)])
    hgt = np.array([s["mean"] if s["mean"] is not None else np.nan for s in
                    zonal_stats(geom, cs["ghs_built_h"], stats=["mean"], all_touched=False, nodata=-200)])

    out = pd.DataFrame({"city": g["city"].values, "subcity_code": g["subcity_code"].values})
    out["pop_zone"] = pop
    out["__built_volume"] = vol
    out["mean_building_height"] = hgt
    return out


def lcz_features(bnd, cs: dict) -> pd.DataFrame:
    """LCZ class-group shares per unit (categorical zonal counts)."""
    from rasterstats import zonal_stats
    g = bnd.to_crs("EPSG:4326")
    cats = zonal_stats(g.geometry.values, cs["lcz"], categorical=True, all_touched=False)
    rows = []
    for cc in cats:
        total = sum(v for k, v in cc.items() if k is not None and k > 0)
        row = {}
        for name, codes in LCZ_GROUPS.items():
            num = sum(cc.get(code, 0) for code in codes)
            row[name] = (num / total) if total > 0 else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    out.insert(0, "subcity_code", g["subcity_code"].values)
    out.insert(0, "city", g["city"].values)
    return out


# ---------------------------------------------------------------------------
def _parse_grd_id(grd):
    """GRD_ID like 'CRS3035RES1000mN2683000E4285000' -> (E, N) lower-left metres."""
    s = grd.split("RES1000m")[-1]
    n = int(s[s.index("N") + 1: s.index("E")])
    e = int(s[s.index("E") + 1:])
    return e, n


def census_features(bnd, cs: dict, res=1000) -> pd.DataFrame:
    """Areal-weighted Eurostat 2021 census grid aggregation to sub-city units.

    Counts are extensive, so each 1 km cell's value is apportioned to a unit by
    the fraction of the cell area that falls inside the unit. Works off the
    parquet table; cell geometries are reconstructed from GRD_ID (EPSG:3035).
    """
    import geopandas as gpd
    from shapely.geometry import box

    g = bnd.to_crs("EPSG:3035")
    pad = res

    cols = ["GRD_ID", "T", "Y_LT15", "Y_1564", "Y_GE65", "EMP",
            "EU_OTH", "OTH", "SAME", "CHG_IN", "CHG_OUT"]
    cen = pd.read_parquet(cs["census_parquet"], columns=cols)
    cen = cen[cen["GRD_ID"].str.startswith("CRS3035")].copy()
    en = cen["GRD_ID"].map(_parse_grd_id)
    cen["E"] = en.map(lambda t: t[0]); cen["N"] = en.map(lambda t: t[1])
    # Keep only cells near an actual city. The cities span Portugal->Finland, so a
    # single continent-wide bounding box would retain ~3M of 4.6M cells and make the
    # overlay below blow up; per-city bounding boxes cut that to a few tens of thousands.
    ub = g.bounds
    ub["__city"] = g["city"].values
    cb = ub.groupby("__city").agg(minx=("minx", "min"), miny=("miny", "min"),
                                  maxx=("maxx", "max"), maxy=("maxy", "max"))
    E = cen["E"].values; N = cen["N"].values
    keep = np.zeros(len(cen), dtype=bool)
    for _, r in cb.iterrows():
        keep |= ((E >= r.minx - pad) & (E <= r.maxx + pad) &
                 (N >= r.miny - pad) & (N <= r.maxy + pad))
    cen = cen[keep].copy()
    for v in cols[1:]:
        cen[v] = pd.to_numeric(cen[v], errors="coerce")
        cen.loc[cen[v].isin(CENSUS_NODATA), v] = np.nan

    cells = gpd.GeoDataFrame(
        cen, geometry=[box(e, n, e + res, n + res) for e, n in zip(cen["E"], cen["N"])],
        crs="EPSG:3035")
    cells["__cell_area"] = cells.geometry.area

    inter = gpd.overlay(g.reset_index(drop=True).reset_index().rename(columns={"index": "__uid"}),
                        cells, how="intersection")
    inter["__w"] = inter.geometry.area / inter["__cell_area"]
    for v in cols[1:]:
        inter[v] = inter[v] * inter["__w"]
    agg = inter.groupby("__uid")[cols[1:]].sum(min_count=1)

    key = g.reset_index(drop=True)[["city", "subcity_code"]]
    agg = key.join(agg)
    T = agg["T"].replace(0, np.nan)
    out = pd.DataFrame({"city": agg["city"], "subcity_code": agg["subcity_code"]})
    out["share_under15"] = agg["Y_LT15"] / T
    out["share_65plus"] = agg["Y_GE65"] / T
    out["employment_rate"] = agg["EMP"] / agg["Y_1564"].replace(0, np.nan)
    out["foreign_born_share"] = (agg["EU_OTH"].fillna(0) + agg["OTH"].fillna(0)) / T
    out["residential_churn"] = (agg["CHG_IN"].fillna(0) + agg["CHG_OUT"].fillna(0)) / T
    out["__census_pop"] = agg["T"]
    return out


# ---------------------------------------------------------------------------
def facility_features(bnd, cs: dict) -> pd.DataFrame:
    """Count of health / education facilities per unit (and a per-capita slot)."""
    import geopandas as gpd
    g = bnd.to_crs("EPSG:3035").reset_index(drop=True)
    g["__uid"] = np.arange(len(g))
    out = pd.DataFrame({"city": g["city"].values, "subcity_code": g["subcity_code"].values})
    for col, path in [("health_fac_n", cs.get("health_facilities")),
                      ("education_fac_n", cs.get("education_facilities"))]:
        if not path:
            out[col] = np.nan
            continue
        pts = _gdf(path, crs="EPSG:3035")
        pts = pts[pts.geometry.notna()]
        j = gpd.sjoin(pts, g[["__uid", "geometry"]], predicate="within", how="inner")
        cnt = j.groupby("index_right").size() if "index_right" in j else j.groupby("__uid").size()
        out[col] = pd.Series(cnt).reindex(g["__uid"]).fillna(0).values
    return out


def centre_distance(bnd, cs: dict) -> pd.DataFrame:
    """Distance (km) from each unit centroid to its city centre (GHS UCDB)."""
    import geopandas as gpd
    g = bnd.to_crs("EPSG:3035").reset_index(drop=True)
    cent = g.geometry.centroid
    out = pd.DataFrame({"city": g["city"].values, "subcity_code": g["subcity_code"].values})
    if not cs.get("ucdb"):
        out["dist_to_centre"] = np.nan
        return out
    ucdb = _gdf(cs["ucdb"], crs="EPSG:3035")
    ucdb_c = ucdb.geometry.centroid
    d = np.full(len(g), np.nan)
    for city, idx in g.groupby("city").groups.items():
        idx = list(idx)
        union = g.loc[idx].geometry.union_all()
        # pick the UCDB centre nearest the city's footprint
        nearest = ucdb_c.distance(union.centroid).idxmin()
        cx, cy = ucdb_c.loc[nearest].x, ucdb_c.loc[nearest].y
        for i in idx:
            d[i] = np.hypot(cent.iloc[i].x - cx, cent.iloc[i].y - cy) / 1000.0
    out["dist_to_centre"] = d
    return out


def age_features(bnd, cs: dict) -> pd.DataFrame:
    """Mean GHSL construction-epoch class per unit (a building-age proxy).

    GHS construction epoch (R2025A; 11 ordinal classes 0..10, nodata 255; EPSG:54009).
    Lower = older fabric. This is the signal the density/LCZ covariates lack -- it
    distinguishes historic cores from modern development. Optional: if `ghs_age` is
    not set the column is NaN (median-imputed downstream).
    """
    if not cs.get("ghs_age"):
        return pd.DataFrame({"city": bnd["city"].values, "subcity_code": bnd["subcity_code"].values,
                             "built_age_mean": np.nan})
    from rasterstats import zonal_stats
    g = bnd.to_crs("ESRI:54009")
    st = zonal_stats(g.geometry.values, cs["ghs_age"], stats=["mean"], nodata=255, all_touched=False)
    out = pd.DataFrame({"city": g["city"].values, "subcity_code": g["subcity_code"].values})
    out["built_age_mean"] = [s["mean"] if s["mean"] is not None else np.nan for s in st]
    return out


# ---------------------------------------------------------------------------
def build(cfg: dict) -> pd.DataFrame:
    cs = cfg["covariate_sources"]
    bnd = load_boundaries(cs, income_labels=cfg.get("io", {}).get("income_labels"))
    bnd_m = bnd.to_crs("EPSG:3035")
    area = pd.DataFrame({
        "city": bnd["city"].values, "subcity_code": bnd["subcity_code"].values,
        "unit_area_km2": (bnd_m.geometry.area / 1e6).values,
    })

    tables = [area,
              ghs_features(bnd, cs),
              lcz_features(bnd, cs),
              census_features(bnd, cs),
              facility_features(bnd, cs),
              centre_distance(bnd, cs),
              age_features(bnd, cs)]
    df = tables[0]
    for t in tables[1:]:
        df = df.merge(t, on=["city", "subcity_code"], how="left")

    # Derived intensities.
    df["pop_density"] = df["pop_zone"] / df["unit_area_km2"].replace(0, np.nan)
    df["built_volume_pc"] = df["__built_volume"] / df["pop_zone"].replace(0, np.nan)
    df["health_fac_pc"] = 1000.0 * df.get("health_fac_n", 0) / df["pop_zone"].replace(0, np.nan)
    df["education_fac_pc"] = 1000.0 * df.get("education_fac_n", 0) / df["pop_zone"].replace(0, np.nan)
    df["city_population"] = df.groupby("city")["pop_zone"].transform("sum")

    keep = ["city", "subcity_code", "pop_zone", "city_population", "unit_area_km2",
            "pop_density", "built_volume_pc", "mean_building_height",
            "lcz_compact_share", "lcz_open_share", "lcz_industrial_share", "lcz_vegetation_share",
            "share_under15", "share_65plus", "employment_rate", "foreign_born_share",
            "residential_churn", "health_fac_pc", "education_fac_pc", "dist_to_centre",
            "built_age_mean"]
    return df[[c for c in keep if c in df.columns]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    cfg = yaml.safe_load(open(ap.parse_args().config))
    out = build(cfg)
    out_path = Path(cfg["covariate_sources"]["out_table"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} units x {out.shape[1]} cols -> {out_path}")
    print("non-null coverage:\n", out.notna().mean().round(3).to_string())


if __name__ == "__main__":
    main()

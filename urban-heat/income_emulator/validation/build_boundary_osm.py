"""Fetch a city's sub-city boundary from OSM as a deploy-ready GeoPackage.

Pulls administrative polygons inside a place, keeps one admin_level, clips to the
place, and writes [boundary_code, city, geometry] -- the schema deploy_predict.py /
covariates.py expect (boundary_code is the Ortsteil/Stadtbezirk name; city groups
the units for the within-city emulator).

    # Berlin Ortsteile (admin_level 10), Munich Stadtbezirke (admin_level 9)
    python validation/build_boundary_osm.py --place "Berlin, Germany"  --admin-level 10 \
        --city Berlin --out data/DE_Berlin_ortsteile.gpkg --layer berlin_ortsteile
    python validation/build_boundary_osm.py --place "Muenchen, Germany" --admin-level 9 \
        --city Munich --out data/DE_Munich_stadtbezirke.gpkg --layer munich_stadtbezirke
"""
from __future__ import annotations
import argparse
import warnings

warnings.filterwarnings("ignore")


def main():
    import osmnx as ox
    ap = argparse.ArgumentParser()
    ap.add_argument("--place", required=True, help='e.g. "Berlin, Germany"')
    ap.add_argument("--admin-level", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layer", required=True)
    ap.add_argument("--place-level", default="4",
                    help="admin_level of the city polygon used to clip (default 4)")
    a = ap.parse_args()

    ox.settings.use_cache = True
    ox.settings.log_console = False
    raw = ox.features_from_place(a.place, tags={"boundary": "administrative"})
    raw = raw[raw.geometry.type.isin(["Polygon", "MultiPolygon"])].copy().to_crs(4326)
    city_name = a.place.split(",")[0].strip()
    city_poly = raw[(raw["admin_level"].astype(str) == a.place_level)
                    & (raw["name"].astype(str).str.contains(city_name, case=False, na=False))
                    ].geometry.union_all()
    units = raw[raw["admin_level"].astype(str) == a.admin_level][["name", "geometry"]].dropna(subset=["name"]).copy()
    units = units.dissolve(by="name", as_index=False)
    units = units[units.geometry.representative_point().within(city_poly)].copy()
    units["boundary_code"] = units["name"]
    units["city"] = a.city
    units = units[["boundary_code", "city", "geometry"]].reset_index(drop=True)
    units.to_file(a.out, layer=a.layer, driver="GPKG")
    print(f"{a.city}: {len(units)} units -> {a.out} (layer {a.layer})")
    print("sample:", sorted(units.boundary_code)[:8])


if __name__ == "__main__":
    main()

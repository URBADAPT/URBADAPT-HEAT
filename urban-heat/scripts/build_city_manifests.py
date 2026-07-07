"""Generate data_manifests/<city>_gdrive.json for the 40-city extension.

Two ingredients per city:
  1. GLOBAL entries  -- one physical Drive file shared by every city (LCZ, the
     vulnerability rasters, GVI, CoolingEff, the 2 AC-NUTS tables, the 143-city
     climate-delta `avg`, the emulator bundle). Their IDs are lifted verbatim
     from an existing pilot manifest (default rome) so they never drift.
  2. PER-CITY FUA entries -- parsed from a links file the user pastes/saves.
     dest is forced to `fua/<slug>_fua_ghs_4326.<ext>` so NB01's glob
     `*<SLUG>*_ghs_4326.*` finds it regardless of the Drive filename.

Income / sub-city boundary / regenerated DRMKC+GVI entries are NOT added here;
they arrive later as their own Drive IDs and get merged in.

Usage:
  python3 scripts/build_city_manifests.py --links fua_links.txt --dry-run   # report only
  python3 scripts/build_city_manifests.py --links fua_links.txt             # write manifests
The --dry-run report shows, per city, which extensions were matched, so a
missing .prj / wrong-city mapping is caught before anything is written.
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # urban-heat/
MAN  = os.path.join(ROOT, "data_manifests")
CFG  = os.path.join(ROOT, "calibration", "preview_configs")

# GLOBAL = same Drive file for every city. Lifted from the pilot manifest at runtime.
GLOBAL_DESTS = [
    "gvi/gvi_356_cities.csv",
    "LCZ/lcz_filter_v3.tif", "LCZ/lcz_v3.tif",
    "CoolingEff/coefs_GVI_lst.csv",
    "vulnerability/GHS_AGE_1975052020_GLOBE_R2025A_54009_100_V1_0.tif",
    "vulnerability/ESTAT_OBS-VALUE-T_2021_V2.tiff",
    "vulnerability/ESTAT_OBS-VALUE-OTH_2021_V2.tiff",
    "vulnerability/ESTAT_OBS-VALUE-EMP_2021_V2.tiff",
    "vulnerability/ESTAT_OBS-VALUE-Y_1564_2021_V2.tiff",
    "ACgridded/ac_penetration_NUTSregions.csv",
    "ACgridded/ac_kwh_NUTSregions.csv",
    "T2MmeanDeltas/climate_change_provide_markups_avg.csv",
    "emulator/bundles/emulator_bundle_pooled_all_quadratic_holdout_safe.json",  # base_kind:outputs
]

# 40 slugs = preview_configs stems. Aliases map the user's Drive/city labels -> slug.
SLUGS = sorted(os.path.splitext(os.path.basename(p))[0]
               for p in glob.glob(os.path.join(CFG, "*.yml")))
ALIAS = {"milano": "milan", "napoli": "naples", "roma": "rome",
         "sevilla": "sevilla", "seville": "sevilla", "koln": "cologne",
         "koeln": "cologne", "wien": "vienna", "praha": "prague",
         "lisboa": "lisbon", "warszawa": "warsaw", "bruxelles": "brussels",
         "bruessel": "brussels", "muenchen": "munich", "munchen": "munich"}

FUA_EXTS = ("shp", "shx", "dbf", "prj", "cpg", "geojson", "gpkg", "json")
ID_RE = re.compile(r"(?:/d/|id=|/file/d/)([A-Za-z0-9_-]{20,})")

def load_global_entries(pilot: str) -> list[dict]:
    m = json.load(open(os.path.join(MAN, f"{pilot}_gdrive.json")))
    by_dest = {e["dest"]: e for e in m["entries"]}
    out = []
    for d in GLOBAL_DESTS:
        if d not in by_dest:
            print(f"  [warn] global dest not in {pilot} manifest: {d}", file=sys.stderr); continue
        e = {"type": "file", "id": by_dest[d]["id"], "dest": d}
        if by_dest[d].get("base_kind"): e["base_kind"] = by_dest[d]["base_kind"]
        out.append(e)
    return out

def norm_city(tok: str) -> str | None:
    t = re.sub(r"[^a-z]", "", tok.lower())
    if t in SLUGS: return t
    if t in ALIAS: return ALIAS[t]
    return None

def parse_links(path: str) -> dict[str, dict[str, str]]:
    """-> {slug: {ext: drive_id}}. Tolerant: tracks a 'current city' header and
    also reads city+ext appearing on the same line as a URL."""
    out: dict[str, dict[str, str]] = {}
    cur = None
    unmatched = []
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if not line: continue
        # city header? (a line with a known city and no URL). Split on _ and . too
        # so a bare filename like `amsterdam_fua_ghs_4326.dbf` yields the token
        # `amsterdam` (the slug) rather than one glued-together string.
        if "http" not in line and "drive.google" not in line:
            for tok in re.split(r"[\s,/;|_.]+", line):
                c = norm_city(tok)
                if c: cur = c; break
            continue
        m = ID_RE.search(line)
        if not m:
            unmatched.append(line); continue
        fid = m.group(1)
        # city on this line overrides current header if present. Filename comes
        # before the URL, so its leading slug token is matched first.
        city = None
        for tok in re.split(r"[\s,/;|_.]+", line):
            c = norm_city(tok)
            if c: city = c; break
        city = city or cur
        # extension from any *.ext token on the line
        ext = None
        me = re.search(r"\.(" + "|".join(FUA_EXTS) + r")\b", line, re.I)
        if me: ext = me.group(1).lower()
        if not city or not ext:
            unmatched.append(f"[city={city} ext={ext}] {line}"); continue
        out.setdefault(city, {})[ext] = fid
    if unmatched:
        print(f"\n[!] {len(unmatched)} link line(s) not mapped (need city+ext):", file=sys.stderr)
        for u in unmatched[:20]: print("    " + u[:140], file=sys.stderr)
    return out

# Observed cities: AC zones use the per-country emulator boundary gpkg (mirrors apply_calibration
# _ZONES_OBS). One gpkg per country, reused across that country's cities; dest boundaries/<file>
# -> data/<city>/boundaries/. The 16 OSM emulator cities fetch OSM live -> no boundary entry.
_OBS_BOUNDARY = {
    "amsterdam": "NL_target_cities_buurten_2023.gpkg", "rotterdam": "NL_target_cities_buurten_2023.gpkg",
    "barcelona": "ES_target_cities_postal_codes.gpkg", "madrid": "ES_target_cities_postal_codes.gpkg",
    "sevilla": "ES_target_cities_postal_codes.gpkg",
    "bologna": "IT_target_cities_CAP.gpkg", "milan": "IT_target_cities_CAP.gpkg",
    "naples": "IT_target_cities_CAP.gpkg", "palermo": "IT_target_cities_CAP.gpkg",
    "brussels": "BE_Brussels_statistical_sectors_2022.gpkg", "dublin": "IE_cities_ED_2022.gpkg",
    "helsinki": "FI_Helsinki_postal_areas_2023.gpkg",
    "lyon": "FR_Paris_Marseille_Lyon_IRIS_current.gpkg", "marseille": "FR_Paris_Marseille_Lyon_IRIS_current.gpkg",
    "paris": "FR_Paris_Marseille_Lyon_IRIS_current.gpkg",
    "stockholm": "SE_target_cities_RegSO_2020.gpkg", "vienna": "AT_Vienna_districts.gpkg",
    "hamburg": "DE_Hamburg_stadtteile.gpkg", "munich": "DE_Munich_stadtbezirke.gpkg",
}

def parse_boundary_links(path: str) -> dict[str, str]:
    """-> {gpkg_filename: drive_id}. Lines like '<file>.gpkg <url>' (tolerant of glued filename+url)."""
    out = {}
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if not line or ("drive.google" not in line and "http" not in line): continue
        m = ID_RE.search(line); fn = re.search(r"([A-Za-z0-9_.\-]+\.gpkg)", line)
        if m and fn: out[fn.group(1)] = m.group(1)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", required=True, help="text file of FUA Drive links")
    ap.add_argument("--pilot", default="rome", help="manifest to lift GLOBAL ids from")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--boundaries", default="", help="text file of per-country boundary gpkg links (observed cities)")
    ap.add_argument("--income", default="", help="Drive link/ID for the shared harmonized income csv (observed cities)")
    ap.add_argument("--only", default="", help="comma list of slugs")
    args = ap.parse_args()

    glob_entries = load_global_entries(args.pilot)
    fua = parse_links(args.links)
    bnd = parse_boundary_links(args.boundaries) if args.boundaries else {}
    inc_id = None
    if args.income:
        _m = ID_RE.search(args.income); inc_id = _m.group(1) if _m else args.income.strip()
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    pilots = {"rome", "athens", "lisbon", "copenhagen"}

    print(f"\nGLOBAL reused: {len(glob_entries)}   |   FUA cities: {len(fua)}   |   boundary gpkgs: {len(bnd)}\n")
    print(f"{'city':13s} {'FUA exts':30s} {'boundary (observed)':38s} status")
    for slug in SLUGS:
        if only and slug not in only: continue
        exts = fua.get(slug, {})
        bfile = _OBS_BOUNDARY.get(slug)
        bnd_entries = ([{"type": "file", "id": bnd[bfile], "dest": f"boundaries/{bfile}"}]
                       if bfile and bfile in bnd else [])
        bcol = (bfile if bnd_entries else (f"{bfile} — NO ID" if bfile else "— (OSM, live)"))
        note = ""
        if slug in pilots: note = "(pilot — skipped)"
        elif not exts:     note = "NO FUA LINKS"
        elif bfile and not bnd_entries: note = "boundary ID missing"
        print(f"  {slug:11s} {','.join(sorted(exts)) or '-':30s} {bcol:38s} {note}")
        if args.dry_run or slug in pilots or not exts: continue
        inc_entries = ([{"type": "file", "id": inc_id, "dest": "income/income_subcity_harmonized.csv"}]
                       if inc_id and slug in _OBS_BOUNDARY else [])
        entries = [{"type": "file", "id": fid, "dest": f"fua/{slug}_fua_ghs_4326.{ext}"}
                   for ext, fid in sorted(exts.items())] + bnd_entries + inc_entries + glob_entries
        out = {"base_subdir": "", "entries": entries}
        with open(os.path.join(MAN, f"{slug}_gdrive.json"), "w") as f:
            json.dump(out, f, indent=2)
    miss = [s for s in SLUGS if s not in pilots and not fua.get(s)]
    if miss: print(f"\n[!] {len(miss)} non-pilot cities with NO FUA links: {miss}")
    print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'wrote manifests -> ' + MAN}")

if __name__ == "__main__":
    main()

"""
Apply the per-country calibration (calibration_params.csv) onto the 40_cities configs.
"""
import argparse, csv, os, re, subprocess, sys, difflib

REPO_DEFAULT = "/Users/armandeaboudrar-meda/Desktop/CMCC/URBADAPT-HEAT"

def git_show(repo, ref, path):
    return subprocess.run(["git","-C",repo,"show",f"{ref}:{path}"],
                          capture_output=True, text=True, check=True).stdout

def set_value(text, section, key, value, comment):
    """Replace `  key: <val>[ # old]` -> `  key: value   # comment`, ONLY inside top-level `section`.
       Returns (new_text, changed_bool). Preserves indentation. Leaves the rest of the file intact."""
    lines = text.split("\n")
    cur = None
    top = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(#.*)?$")
    kre = re.compile(rf"^(\s*){re.escape(key)}:\s*\S.*$")
    changed = False
    for i, ln in enumerate(lines):
        m = top.match(ln)
        if m: cur = m.group(1)
        if cur == section and kre.match(ln):
            indent = re.match(r"^(\s*)", ln).group(1)
            c = f"   # {comment}" if comment else ""
            lines[i] = f"{indent}{key}: {value}{c}"
            changed = True
            break
    return "\n".join(lines), changed

def ensure_income_source(text, value="observed"):
    """Insert `  source: <value>` as first child of `income:` if no `source:` already there.
       value comes from income_source_inventory.csv (observed | emulator)."""
    if value == "emulator":
        comment = "observed | emulator -- EMULATOR: still needs an emulator: block + generated <city>_p_inc.csv (repo-relative path)"
    else:
        comment = "observed | emulator (NB05 income source; see cityheat.income_source)"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"^income:\s*(#.*)?$", ln):
            # scan the income block for an existing `source:`
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip()=="" ):
                if re.match(r"^\s+source:\s", lines[j]):
                    return text, False
                # stop at next top-level key
                if re.match(r"^[A-Za-z_]", lines[j]): break
                j += 1
            lines.insert(i+1, f"  source: {value}          # {comment}")
            return "\n".join(lines), True
    return text, False

def edits_for(row, ac_basis, tree_ctx, set_fixed):
    cap_user = row[f"capex_per_user_{ac_basis}"]
    base_note = "€743 2018 EU base (product-only)" if ac_basis=="product" else "€1543 2018 installed (product+€800)"
    tcap = row[f"capex_per_tree_{tree_ctx}"]; tom = row[f"om_per_tree_{tree_ctx}"]
    tnote = "sealed/street shade" if tree_ctx=="street" else "park/low-engineering"
    # EWS 3-tier taxonomy (ews_taxonomy_assumptions Definitions v4): country -> tier -> setup CAPEX
    _ews_tier = {"IT":1,"FR":1,"ES":1,"PT":1,                                   # Tier1 epidemiological/mature -> 0
                 "DE":2,"NL":2,"BE":2,"AT":2,"HU":2,"RO":2,"SE":2,"FI":2,        # Tier2 meteorological/intermediate
                 "GR":3,"HR":3,"SI":3,"BG":3,"SK":3,"CZ":3,"PL":3,"DK":3,"IE":3,"EE":3,"LV":3,"LT":3}  # Tier3 traffic-light/weak -> full
    _ews_capex = {1: 0, 2: 75000, 3: 150000}   # 0 / intermediate(~half, expert) / full (~$150k, Ebi 2004 via Takacs)
    _ews_tname = {1: "epidemiological/mature", 2: "meteorological/intermediate", 3: "traffic-light/weak"}
    E = [
        ("ac","capex_per_user",   cap_user, f"{base_note} x PLI {row['PLI']}/100 [CALIB_AC S5]"),
        ("ac","maint_rate",       0.04,     "prEN 15459:2006 via Lot-10: 4%/yr of purchase+install [CALIB_AC S3]"),
        ("ac","lifetime_years",   12,       "Lot-10 Task4 / 2018 review; UQ range 9-16 [CALIB_AC S3]"),
        ("trees","capex_per_tree_eur",        tcap, f"{tnote} base x PLI {row['PLI']}/100 [D5.1/Prague; CALIB_AC S7]"),
        ("trees","om_per_tree_per_year_eur",  tom,  f"{tnote} O&M base x PLI/100 [D5.1/Vogt; CALIB_AC S7]"),
        ("ews","opex_per_warning_day_basic",       row["opex_basic"],       f"Chiabai 2018 €7800 Madrid x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","opex_per_warning_day_enhanced",    row["opex_enhanced"],    f"Chiabai €14000 x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","opex_per_warning_day_incremental", row["opex_incremental"], f"Chiabai increment €6200 x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","usd_per_capita_per_day", 0.014, "Pavanello & Valenti 2025 (WP 35.2025) via Ebi 2004 [CALIB_EWS S6]"),
    ]
    # opex_annual_fixed: current decision -> always write Rao 2025 standing HHWS cost (€200k/yr, PLI-scaled)
    if row.get("opex_annual_fixed_if_enabled"):
        E.append(("ews","opex_annual_fixed", row["opex_annual_fixed_if_enabled"], "Rao 2025 standing HHWS cost €200k/yr x PLI/PLI_ES [CALIB_EWS S5]"))
    # ramp already 3 / 0.10 in config -> re-write with the Pavanello Fig 4(c) citation
    E.append(("ews","ramp_years", 3, "Pavanello & Valenti 2025 Fig 4(c): HHWWS effect emerges after ~yr3 [CALIB_EWS S6]"))
    E.append(("ews","ramp_initial_efficacy", 0.10, "Pavanello Fig 4(c): near-zero yr0-3 then ramps [CALIB_EWS S6]"))
    # new_build_vulnerability: constant EU-wide (EPBD nZEB convergence + age already differentiated in the model)
    E.append(("vulnerability","new_build_vulnerability", 0.15, "constant EU-wide: EPBD nZEB (all new builds >=2021) + age already differentiated; nZEB overheating = UQ sensitivity [CALIB]"))
    if row.get("retrofit_rate_per_year"):   # under vulnerability: -> dynamic: -> thermal_projection: (key unique per file)
        E.append(("vulnerability","retrofit_rate_per_year", row["retrofit_rate_per_year"],
                  "EC 2019 weighted renovation rate (Table2 rate x Table4 savings; winter-PE proxy) [calibration/retrofit_source_ec2019.csv]"))
    if row.get("pct_reduction_per_gvi_point"):   # under electricity_feedback: (key unique per file)
        ph = str(row.get("pct_gvi_is_placeholder","")).lower() in ("1","true","yes")
        cm = ("PLACEHOLDER = flat 0.008 (Falchetta 2026 Fig5 @~30C); recompute per-city from JJA-Tmax once "
              "city_summer_tmax.csv is filled [calibration/gen_gvi_reduction.py]") if ph else \
             "Falchetta 2026 Fig5 anchors interpolated @ city JJA-Tmax [calibration/gen_gvi_reduction.py]"
        E.append(("electricity_feedback","pct_reduction_per_gvi_point", row["pct_reduction_per_gvi_point"], cm))
    _tier = _ews_tier.get(row["country"])
    if _tier:
        E.append(("ews","capex_setup", _ews_capex[_tier],
                  f"3-tier EWS taxonomy Tier {_tier} ({_ews_tname[_tier]}): 0/75k/150k setup CAPEX; full=$150k Ebi2004 via Takacs [ews_taxonomy_v4]"))
    return E

# AC-downscaling zones for the 16 boundary-less emulator cities => OSM_REGIONS_REFERENCE.md
# Already set each city's regions: (veg, 8-25 zones) + osm.query. This fills the zones: (AC)
# block: a FINER OSM level where his notes flag one, else the SAME level as regions (shared). match_by
# = name (OSM units are name-keyed; emulator income is tabular, not raster/spatial_join). The 6 finer
# cities also get the finer level added to osm.admin_levels so it downloads. ljubljana has no OSM
# level -> it reuses the external shapefile set for the regions.
_ZONES_AC = {
    # slug: (zones.osm_admin_level, osm.admin_levels-or-None, ac_note, veg_note)
    "berlin":       (10, "[10, 9, 8, 7]",  "L10 Ortsteile 97",      "L9 Bezirke 12"),
    "cologne":      (10, "[10, 9, 8, 7]",  "L10 Stadtteile ~86",    "L9 Stadtbezirke 9"),
    "nantes":       (11, "[11, 10, 8, 7]", "L11 sub-quartiers ~94", "L10 quartiers 11"),
    "prague":       (9,  "[9, 8, 7, 6]",   "L9 mestske casti ~57",  "L6 spravni obvody 22"),
    "riga":         (10, "[10, 9, 8, 7]",  "L10 apkaimes ~58",      "L9 apkaimes 7"),
    "sofia":        (9,  "[9, 8, 7, 6]",   "L9 zhk/kvartali ~120",  "L6 rayoni 22"),
    "zagreb":       (10, "[10, 9, 8, 7]",  "L10 mjesni odbori",     "L9 gradske cetvrti 17"),
    "porto":        (8,  None, "L8 freguesias 7 (=veg)",     "L8 freguesias 7"),
    "thessaloniki": (9,  None, "L9 communities 6 (=veg)",    "L9 communities 6"),
    "bratislava":   (9,  None, "L9 mestske casti 17 (=veg)", "L9 mestske casti 17"),
    "bucharest":    (9,  None, "L9 sectoare 6 (=veg)",       "L9 sectoare 6"),
    "budapest":     (9,  None, "L9 keruletek 23 (=veg)",     "L9 keruletek 23"),
    "varna":        (6,  None, "L6 rayoni 5 (=veg)",         "L6 rayoni 5"),
    "tallinn":      (9,  None, "L9 linnaosad 8 (=veg)",      "L9 linnaosad 8"),
    "vilnius":      (10, None, "L10 seniunijos 21 (=veg)",   "L10 seniunijos 21"),
    "warsaw":       (9,  None, "L9 dzielnice 18 (=veg)",     "L9 dzielnice 18"),
}

# The 19 OBSERVED cities: income comes from the harmonized sub-city table, keyed to the emulator's
# per-country boundary (boundary_code). AC zones therefore = that boundary (shapefile). NB05 clips it
# to the FUA (gpd.overlay intersection), so the shared per-country gpkg works as-is (no per-city slice).
# match_by: "code" only for 5-digit numeric POSTAL (to_cap5 keeps last 5 digits); "name" for everything
# else (IRIS/buurt/UUID/RegSO/sector/German names) -- to_cap5 would truncate & mis-join those. Formats
# verified against income_subcity_harmonized (subcity_code samples), 2026-07-07.
_ZONES_OBS = {
    # slug: (boundary gpkg under data/<city>/, shapefile_zone_col, match_by, note)
    "amsterdam": ("boundaries/NL_target_cities_buurten_2023.gpkg", "boundary_code", "name", "buurten ~147 (alnum)"),
    "rotterdam": ("boundaries/NL_target_cities_buurten_2023.gpkg", "boundary_code", "name", "buurten ~64 (alnum)"),
    "barcelona": ("boundaries/ES_target_cities_postal_codes.gpkg", "boundary_code", "code", "postal 5-digit ~40"),
    "madrid":    ("boundaries/ES_target_cities_postal_codes.gpkg", "boundary_code", "code", "postal 5-digit ~55"),
    "sevilla":   ("boundaries/ES_target_cities_postal_codes.gpkg", "boundary_code", "code", "postal 5-digit ~20"),
    "bologna":   ("boundaries/IT_target_cities_CAP.gpkg", "boundary_code", "code", "CAP 5-digit ~19"),
    "milan":     ("boundaries/IT_target_cities_CAP.gpkg", "boundary_code", "code", "CAP 5-digit ~38"),
    "naples":    ("boundaries/IT_target_cities_CAP.gpkg", "boundary_code", "code", "CAP 5-digit ~25"),
    "palermo":   ("boundaries/IT_target_cities_CAP.gpkg", "boundary_code", "code", "CAP 5-digit ~26"),
    "brussels":  ("boundaries/BE_Brussels_statistical_sectors_2022.gpkg", "boundary_code", "name", "sectors ~670 (alnum)"),
    "dublin":    ("boundaries/IE_cities_ED_2022.gpkg", "boundary_code", "name", "ED ~322 (UUID)"),
    "helsinki":  ("boundaries/FI_Helsinki_postal_areas_2023.gpkg", "boundary_code", "code", "postal 5-digit ~83"),
    "lyon":      ("boundaries/FR_Paris_Marseille_Lyon_IRIS_current.gpkg", "boundary_code", "name", "IRIS 9-digit ~169"),
    "marseille": ("boundaries/FR_Paris_Marseille_Lyon_IRIS_current.gpkg", "boundary_code", "name", "IRIS 9-digit ~336"),
    "paris":     ("boundaries/FR_Paris_Marseille_Lyon_IRIS_current.gpkg", "boundary_code", "name", "IRIS 9-digit ~870"),
    "stockholm": ("boundaries/SE_target_cities_RegSO_2020.gpkg", "boundary_code", "name", "RegSO ~127 (alnum)"),
    "vienna":    ("boundaries/AT_Vienna_districts.gpkg", "boundary_code", "code", "districts 5-digit ~23"),
    "hamburg":   ("boundaries/DE_Hamburg_stadtteile.gpkg", "boundary_code", "name", "stadtteile ~99 (names)"),
    "munich":    ("boundaries/DE_Munich_stadtbezirke.gpkg", "boundary_code", "name", "stadtbezirke ~25 (names, one '8.2' stray)"),
}

def zones_edits_for(city):
    """AC `zones:` block edits for every NEW city (OSM emulator, shapefile observed, ljubljana). Pilots -> []."""
    if city in _ZONES_OBS:    # observed city -> AC zones = its observed-income (emulator) boundary
        shp, col, mb, note = _ZONES_OBS[city]
        return [
            ("zones","source", '"shapefile"', f"AC = observed-income boundary ({note}); NB05 clips to FUA [emulator boundaries]"),
            ("zones","shapefile", f'"{shp}"', "shared per-country emulator gpkg; place in data/<city>/ via manifest"),
            ("zones","shapefile_zone_col", '"'+col+'"', "emulator key col = harmonized subcity_code"),
            ("zones","match_by", '"'+mb+'"', "code=5-digit postal (to_cap5); name=exact norm (no truncation)"),
        ]
    if city == "ljubljana":   # no OSM sub-city level -> reuse the regions external shapefile
        return [
            ("zones","source", '"shapefile"', "no OSM sub-city level; reuse regions shapefile [OSM_REF D]"),
            ("zones","shapefile", '"boundaries/ljubljana_cetrtne.geojson"', "17 cetrtne skupnosti (same as regions)"),
            ("zones","shapefile_zone_col", '"name"', "VERIFY column name on download [OSM_REF D]"),
            ("zones","match_by", '"name"', "name-keyed join (emulator income is tabular)"),
        ]
    if city not in _ZONES_AC:
        return []
    lvl, admin_levels, ac, veg = _ZONES_AC[city]
    E = [
        ("zones","osm_admin_level", lvl, f"AC downscaling: OSM {ac} [OSM_REGIONS_REFERENCE]"),
        ("zones","match_by", '"name"', "OSM name-keyed; emulator income tabular (not spatial_join)"),
    ]
    if admin_levels:
        E.append(("osm","admin_levels", admin_levels, f"added AC level (finer than veg {veg}) [OSM_REF]"))
    return E

def wire_emulator_income(text, city):
    """Emulator cities: set aggregation=mean and insert the income.emulator: block if absent.
    csv = per-city deploy output (income_emulator/validation/deploy_new_city.sh -> <city>_p_inc.csv),
    placed under data/<city>/emulated/ via the manifest. Reuses the config's income.city_aliases.
    NOTE: the emulator csv is read RAW by load_emulator_inc_agg -> needs the P()-resolve code fix
    (calibration/EMULATOR_INCOME_CODE_FIX.md) before a repo-relative path works."""
    if re.search(r'^\s+emulator:\s*$', text, re.M):                     # already wired
        return set_value(text, "income", "aggregation", "mean", "emulator index is per-zone mean")[0], False
    m = re.search(r'^income:.*?^\s+city_aliases:\s*(\[[^\]]*\])', text, re.M | re.S)
    aliases = m.group(1) if m else f'["{city.capitalize()}"]'
    text = set_value(text, "income", "aggregation", "mean", "emulator index is per-zone mean (NOT weighted)")[0]
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r'^income:\s*(#.*)?$', ln):
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
                if re.match(r'^\s+source:\s', lines[j]):
                    block = [
                        "  emulator:",
                        f'    csv: "emulated/{city}_p_inc.csv"          # deploy_new_city.sh output; needs P()-resolve fix + manifest',
                        f"    city_aliases: {aliases}",
                        "    columns: {city: city, zone_id: subcity_code, income: income_index_pred, count: pop_zone}",
                        "    aggregation: mean",
                        "    min_coverage: 0.95",
                    ]
                    for k, b in enumerate(block):
                        lines.insert(j + 1 + k, b)
                    return "\n".join(lines), True
                if re.match(r'^[A-Za-z_]', lines[j]):
                    break
                j += 1
            break
    return "\n".join(lines), False

def wire_observed_income(text, city):
    """Observed cities (the 19 new ones): point files.income_csv at the shared harmonized table,
    set the column mapping, and aggregation=mean. Both zone_id AND zone_name map to subcity_code so
    either match_by joins code-to-code against the boundary's boundary_code (NB05 code->to_cap5 /
    name->norm_name, both on the SAME code). income = inc_pct_within_city (harmonized within-city
    income percentile 0-1; AC ranks it). NB05 filters the multi-city file by income.city_aliases and
    P()-resolves the csv, so no code fix needed (unlike the emulator path)."""
    # harmonized `city` string differs from the slug for a few cities -> include both (verified
    # against income_subcity_harmonized 2026-07-07; else the isin() filter returns 0 rows = flat income)
    _alias = {"milan": '["Milan", "Milano"]', "naples": '["Naples", "Napoli"]',
              "brussels": '["Brussels", "Brussels-Capital Region"]'}
    if city in _alias:
        text = set_value(text, "income", "city_aliases", _alias[city],
                         "harmonized `city` differs from slug -> include the harmonized spelling")[0]
    text = set_value(text, "files", "income_csv", '"income/income_subcity_harmonized.csv"',
                     "shared harmonized sub-city income; NB05 filters by income.city_aliases")[0]
    text = set_value(text, "income", "aggregation", "mean",
                     "one row per (city,subcity_code); no pop-count col -> mean")[0]
    new_cols = (
        "  columns:\n"
        "    city: city\n"
        "    zone_id: subcity_code\n"
        "    zone_name: subcity_code       # harmonized code; matches boundary_code under norm_name\n"
        "    income: inc_pct_within_city   # within-city income percentile 0-1 (AC ranks this)\n"
        "    count: null\n"
        "    year: null                    # (city,subcity_code) unique -> one row/zone, no year filter\n"
    )
    text2 = re.sub(r'^  columns:\n(?:    .*\n)+', new_cols, text, count=1, flags=re.M)
    return text2, True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--ref", default="origin/40_cities_implementation", help="git ref to read base configs from")
    ap.add_argument("--csv", default=os.path.join(os.path.dirname(__file__), "calibration_params.csv"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "preview_configs"))
    ap.add_argument("--inventory", default=os.path.join(os.path.dirname(__file__), "income_source_inventory.csv"),
                    help="per-city income source (observed|emulator) from the IncomeDatabase; flat 'observed' if absent")
    ap.add_argument("--ac-basis", choices=["product","installed"], default="product")
    ap.add_argument("--tree-context", choices=["street","park"], default="street")
    ap.add_argument("--set-opex-fixed", action="store_true", help="also write opex_annual_fixed (else leave config's 0)")
    ap.add_argument("--only", default="", help="comma list of cities (default all in CSV)")
    ap.add_argument("--diff", action="store_true", help="print a unified diff per city")
    args = ap.parse_args()

    rows = {r["city"]: r for r in csv.DictReader(open(args.csv))}
    inv = {}
    if os.path.exists(args.inventory):
        inv = {r["city"]: r["income_source"] for r in csv.DictReader(open(args.inventory))}
    only = [c.strip() for c in args.only.split(",") if c.strip()] or list(rows)
    os.makedirs(args.out, exist_ok=True)
    n_obs = sum(1 for c in only if inv.get(c, "observed") == "observed")
    print(f"basis: AC={args.ac_basis}  tree={args.tree_context}  opex_fixed={'on' if args.set_opex_fixed else 'left as config'}"
          f"  | income: {n_obs} observed / {len(only)-n_obs} emulator\n")
    for city in only:
        row = rows[city]; path = f"urban-heat/configs/{city}.yml"
        try:
            orig = git_show(args.repo, args.ref, path)
        except subprocess.CalledProcessError:
            print(f"  {city}: SKIP (not in {args.ref})"); continue
        text = orig; applied = []
        for section,key,val,comment in edits_for(row, args.ac_basis, args.tree_context, args.set_opex_fixed) + zones_edits_for(city):
            text, ch = set_value(text, section, key, val, comment)
            applied.append(f"{section}.{key}={val}" if ch else f"!{section}.{key}(missing)")
        src = inv.get(city, "observed")
        text, inc = ensure_income_source(text, src)
        if inc: applied.append(f"income.source={src}")
        if src == "emulator":
            text, ew = wire_emulator_income(text, city)
            if ew: applied.append("income.emulator")
        elif city in _ZONES_OBS:              # 19 new observed cities (pilots keep their own income)
            text, ow = wire_observed_income(text, city)
            if ow: applied.append("income.observed")
        open(os.path.join(args.out, f"{city}.yml"), "w").write(text)
        miss = [a for a in applied if a.startswith("!")]
        print(f"  {city:12s} {len([a for a in applied if not a.startswith('!')])} edits" + (f"  MISSING:{miss}" if miss else ""))
        if args.diff:
            for dl in difflib.unified_diff(orig.split("\n"), text.split("\n"), f"a/{city}", f"b/{city}", lineterm=""):
                if dl.startswith(("+","-")) and not dl.startswith(("+++","---")): print("     "+dl)
    print(f"\npreviews -> {args.out}")

if __name__ == "__main__":
    main()

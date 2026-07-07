# Data needed to run `March2026_agnostic` for all 40 cities (Drive-manifest TODO)

Goal: a fresh clone with **no local data** can run NB01–10 for every city with no errors.
Built by inspecting the actual notebooks, `cityheat/income_source.py`, the configs, and the
on-disk tables (2026-07-07). Nothing here is assumed — items I could not verify are marked ⚠.

## How the plumbing works (so you know what an "entry" is)
- Each `configs/<city>.yml` has `drive_manifest: data_manifests/<city>_gdrive.json`.
- **NB01 cell-5** reads that manifest and, for every entry `{ "id": <DriveFileID>, "dest": "<relpath>" }`,
  downloads the Drive file into `data/<city>/<dest>` (or `outputs.../<city>/<dest>` if `"base_kind":"outputs"`).
- So **"add data to the manifest" = give me the Drive file ID + the dest path.** I write the 36 JSON files.
- The 4 pilots (rome, athens, lisbon, copenhagen) already have manifests. **36 new cities have none**
  (`# TODO: create Drive manifest` in Giacomo's configs).

---

## A. GLOBAL / SHARED — reuse the EXISTING Drive IDs, upload NOTHING
These are one physical file used by every city; the pilot manifests all point at the **same** Drive ID.
I copy those IDs into each new city's manifest. **No new upload, no new folder.**

| dest (under `data/<city>/`) | what it is |
|---|---|
| `gvi/gvi_356_cities.csv` | green-view index, 356 cities |
| `LCZ/lcz_filter_v3.tif`, `LCZ/lcz_v3.tif` | Local Climate Zones (global raster, masked to FUA in-nb) |
| `CoolingEff/coefs_GVI_lst.csv` | cooling-effect coefficients |
| `vulnerability/GHS_AGE_1975052020_GLOBE_R2025A_54009_100_V1_0.tif` | global age raster |
| `vulnerability/ESTAT_OBS-VALUE-{T,OTH,EMP,Y_1564}_2021_V2.tiff` (4) | Eurostat census rasters |
| `ACgridded/ac_penetration_NUTSregions.csv`, `ACgridded/ac_kwh_NUTSregions.csv` | EU-wide NUTS AC tables |
| `T2MmeanDeltas/climate_change_provide_markups_avg.csv` | **PROVIDE deltas — one long table, 143 cities, already incl. all 40** ✅ |
| `emulator/bundles/emulator_bundle_pooled_all_quadratic_holdout_safe.json` | trained emulator (`base_kind:"outputs"`) |

⚠ `T2MmeanDeltas/..._bands.csv` and `..._gcm.csv` had a *different* ID per pilot city. `_avg` is a single
143-city table, so `_bands`/`_gcm` are almost certainly the same all-city tables uploaded as copies —
**verify one spans the new cities**; if so, reuse one ID for all.

---

## B. PER-CITY — must exist on Drive for each of the 36 new cities

### B1. FUA polygon (every city) — **must create + upload**
- NB01 globs `data/<city>/fua/*<slug>*_ghs_4326.*`. Provide the 5 parts:
  `fua/<slug>_fua_ghs_4326.{shp,shx,dbf,prj,geojson}` (GHS-FUA database, EPSG:4326).
- Source = GHS Functional Urban Areas; clip the global GHS-FUA layer to each city. Not auto-downloaded.

### B2. Income table (every city) — source depends on `income_source`
`calibration/income_source_inventory.csv` splits the 40: **23 observed / 17 emulator**.
- **observed** → the measured sub-city income CSV (like Rome's IRPEF), dest `income/<file>.csv`, wired via `files.income_csv`.
  Good news: all 19 *new* observed cities are present in `income_emulator/data/income/income_subcity_harmonized.csv`
  → split per city + upload (or point the config at a shared harmonized CSV filtered by `city_aliases`).
- **emulator** → the emulator's per-zone prediction `<city>_p_inc.csv` (cols: city, subcity_code, income_index_pred, pop_zone),
  dest e.g. `emulated/<city>_p_inc.csv`, wired via `income.source: emulator` + `income.emulator.csv`.
  **Only `rome_p_inc.csv` exists today** → the other 16 emulator cities must be GENERATED first (see §E).
  ⚠ Fix the Rome config's absolute `/Users/...` emulator path to repo-relative before any PR.

---

## C. PER-COUNTRY sub-city boundaries — upload once per country (shared)
Used by NB05 to define AC-downscaling zones **and** by the emulator to predict income. These are the
`income_emulator/data/boundaries/<COUNTRY>_*.gpkg` files. Put them once in a shared Drive folder
(e.g. `CityAgnostic/boundaries/`); I reference the same ID from each city of that country.

Present today (covers all 23 observed + berlin): BE, CH, DK, ES, FI, FR(Paris/Marseille/Lyon + Strasbourg/Bordeaux),
GB, GR(Athens), IE, IT, NL, PT(Lisbon), SE, AT, DE(Munich, Hamburg, Dresden, Berlin), NO.

**⚠ MISSING boundary (16 of 17 emulator cities)** — no sub-city layer exists, so neither the emulator nor
NB05 zoning can run until one is sourced (national statistical units or OSM admin):
`cologne(DE)`, `nantes(FR)`, `porto(PT)`, `thessaloniki(GR)`, `bratislava(SK)`, `bucharest(RO)`,
`budapest(HU)`, `ljubljana(SI)`, `prague(CZ)`, `riga(LV)`, `sofia(BG)`, `varna(BG)`, `tallinn(EE)`,
`vilnius(LT)`, `warsaw(PL)`, `zagreb(HR)`. (berlin already has `DE_Berlin_ortsteile.gpkg`.)
*Fallback if no boundary:* keep `zones.source: osm` (live OSM admin) for AC downscaling, but the emulator
still needs a polygon layer to produce income → those cities have no income until this is resolved.

---

## D. REGENERATE then upload (script-generated, currently PILOT-ONLY)
These feed the dynamic vulnerability projection (NB03/NB09) and today cover only DK/GR/IT/PT:
- `vulnerability/drmkc_vulnerability_projection.csv` — DRMKC LVI, **NUTS-3, only 4 countries now**
  → rerun `scripts/download_drmkc_vulnerability.py` (needs JWT) for all new countries.
- `vulnerability/gvi_projections.csv` — GDL-GVI country×SSP, **only GRC/ITA/PRT now**
  → rerun `scripts/prepare_gvi_projections.py` for all new ISO3.
Each is one table keyed by region/country, so once regenerated for all EU it can be a **single shared ID**.

---

## E. Emulator generation (prerequisite for the 17 emulator cities' income)
To produce the missing `<city>_p_inc.csv`, run `income_emulator/` (covariates.py → run.py/deploy_predict.py).
It needs (see `income_emulator/config_urbadapt.yaml`):
- the per-country boundary gpkgs (§C) — **16 are missing** (the real blocker);
- income labels `income_subcity_harmonized.csv` — ⚠ currently a **symlink** to
  `~/Desktop/CMCC/adaptation_infrastructure_database/IncomeDatabase/` → won't sync; ship the real file;
- `gisco/ESTAT_Census_2021_V3.parquet`, `EU_health_facilites.gpkg`, `EU_education_facilites.gpkg`;
- GHS_POP / GHS_BUILT / UCDB rasters + LCZ — ⚠ hardcoded to Giacomo's OneDrive Windows paths.
Output `results/income_index_predictions.csv` already holds all trained cities → can serve as the shared
emulator income file (resolver filters by `city_aliases`), avoiding 17 separate uploads.

---

## F. LIVE API — nothing on Drive (auto-download at runtime)
- **UrbClim T2M** (NB01/02) — per-city URL already in each config's `climate.urbclim_api.t2m_url` ✅ (verified for berlin/madrid).
- **WorldPop age rasters** (NB02) — from `WP_ISO3`+`WP_YEARS`.
- **WCDE/Wittgenstein mortality** (NB04, R subprocess).
- **OSM** region/zone boundaries (NB05/07) when `source: osm` (Giacomo's new configs default to osm).

---

## What I need from you (fill IDs → I write the 36 manifests)
1. **Confirm §A reuse**: I lift the 13 global IDs from the pilot manifests — OK? (+ verify `_bands/_gcm`.)
2. **Per new-city folder** (Berlin/, Madrid/, …) upload → send `city → {fua ID×5, income-file ID}`.
3. **Per-country boundaries** → send `country → boundary.gpkg ID` (once each).
4. **After regeneration** → send `drmkc ID`, `gvi ID` (all-country versions).
5. **Decide** the 16 boundary-less emulator cities: source a boundary, or drop them to observed/osm, or defer.

<!-- ID FILL-IN TABLES (I complete as you send) -->
### new observed cities (19) — need FUA + observed income
amsterdam(NL) barcelona(ES) bologna(IT) brussels(BE) dublin(IE) hamburg(DE) helsinki(FI) lyon(FR)
madrid(ES) marseille(FR) milan(IT) munich(DE) naples(IT) palermo(IT) paris(FR) rotterdam(NL)
sevilla(ES) stockholm(SE) vienna(AT)
### new emulator cities (17) — need FUA + boundary + emulator run
berlin(DE) bratislava(SK) bucharest(RO) budapest(HU) cologne(DE) ljubljana(SI) nantes(FR) porto(PT)
prague(CZ) riga(LV) sofia(BG) tallinn(EE) thessaloniki(GR) varna(BG) vilnius(LT) warsaw(PL) zagreb(HR)
</content>

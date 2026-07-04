"""
Generate per-country calibration parameters for the 40-city URBADAPT-HEAT extension.

Source of every base value & rule (all opened & verified this session):
  - CALIBRATION_AC_TREES_economics.md  (Lot-10 2008 Table 2-8; 2018 Review base case
    743/1543/4%/12yr; Pauleit trees; Prague Horvathova 2021; 100KTREEs D5.1; Eurostat PLI)
  - CALIBRATION_EWS.md                  (Chiabai 7800/14000/6200; Rao 200k fixed; Pavanello
    2025 0.014 & ramp 3/0.10; efficacy triangulation)
  - Calibration_40citiesExtension.pdf   (consolidation of the two above)

NOTHING here is hand-computed: all per-country numbers are derived by this script from the
documented bases x the Eurostat price-level index (PLI, EU=100, household final consumption, 2025).

Decision switches (set with Giacomo) are the UPPER-CASE constants below. The CSV emits BOTH
options for the open decisions (AC product vs installed; tree street vs park) so nothing is
prejudged; apply_calibration.py picks which column to write.
"""
import csv, os

# Eurostat PLI (EU=100, 2025)
PLI = {
    "IS":173.5,"CH":171.3,"DK":139.7,"IE":136.2,"LU":131.5,"NO":128.6,"SE":121.0,"FI":120.8,
    "BE":116.2,"NL":115.6,"AT":113.0,"FR":110.3,"DE":108.3,"EE":101.2,"IT":97.1,"MT":91.9,
    "ES":91.6,"CZ":89.4,"SI":89.3,"CY":89.2,"GR":87.4,"PT":86.6,"SK":85.2,"LV":83.2,
    "LT":82.8,"HR":78.4,"HU":77.5,"PL":73.3,"RO":65.1,"BG":62.5,
}
PLI_SPAIN = PLI["ES"]  # 91.6 -> EWS opex reference (Chiabai's Madrid base)

# 40 cities -> country + region
# region drives the EWS efficacy archetype (PROVISIONAL: confirm per-country vs Casanueva 2019
# HHWS inventory + Climate-ADAPT before finalising). S=Southern W=Western/Central N=Northern E=Eastern
CITY = {
    "amsterdam":("NL","W"), "athens":("GR","S"), "barcelona":("ES","S"), "berlin":("DE","W"),
    "bologna":("IT","S"), "bratislava":("SK","E"), "brussels":("BE","W"), "bucharest":("RO","E"),
    "budapest":("HU","E"), "cologne":("DE","W"), "copenhagen":("DK","N"), "dublin":("IE","W"),
    "hamburg":("DE","W"), "helsinki":("FI","N"), "lisbon":("PT","S"),
    "ljubljana":("SI","E"), "lyon":("FR","W"), "madrid":("ES","S"), "marseille":("FR","S"),
    "milan":("IT","S"), "munich":("DE","W"), "nantes":("FR","W"), "naples":("IT","S"),
    "palermo":("IT","S"), "paris":("FR","W"), "porto":("PT","S"), "prague":("CZ","E"),
    "riga":("LV","E"), "rome":("IT","S"), "rotterdam":("NL","W"), "sevilla":("ES","S"),
    "sofia":("BG","E"), "stockholm":("SE","N"), "tallinn":("EE","E"), "thessaloniki":("GR","S"),
    "varna":("BG","E"), "vienna":("AT","W"), "vilnius":("LT","E"), "warsaw":("PL","E"),
    "zagreb":("HR","S"),
}
ARCHETYPE = {"S":"mature_hhwws","W":"mature_hhwws","N":"mature_hhwws","E":"weak_threshold"}

#  documented EU-central bases
# AC (2018 Review final report, Base Case 1 = 3.5 kW reversible single split), 2018 EUR:
AC_PRODUCT_2018   = 743.0     # product only ("given in Task 2")
AC_INSTALLED_2018 = 1543.0    # product + 800 install
# Trees, 2024 EUR (Pauleit infl. central ~700 park; D5.1 sealed/Prague street ~1600-3500 -> central 2500):
TREE_STREET_2024  = 2500.0    # sealed/hardscape shade tree (D5.1 "sealed" bracket / Prague anchor)
TREE_PARK_2024    = 700.0     # park / low-engineering (Pauleit central, PDF illustrative base)
OM_STREET_2024    = 150.0     # steady-state street O&M (D5.1 sealed 70-268 central; NOT the 372 5-yr peak)
OM_PARK_2024      = 65.0      # Vogt active pruning/watering central (50-85)
# EWS (Chiabai Table 4, base 2013 EUR, Madrid):
EWS_BASIC_2013       = 7800.0
EWS_ENHANCED_2013    = 14000.0
EWS_INCREMENTAL_2013 = 6200.0
EWS_FIXED_2013       = 200000.0   # Rao 2025 standing annual cost (optional; config currently 0)

# Inflation to a common base year. Left at 1.0 = base-year values
# (2018 for AC, 2024 for trees, 2013 for EWS). Flag in NOTES; do not fabricate an index here.
INFL_AC_2018   = 1.0
INFL_EWS_2013  = 1.0

def sc(base, iso):            # PLI-scale on EU=100
    return base * PLI[iso] / 100.0
def sc_es(base, iso):         # EWS: scale on Spain=91.6 (Chiabai Madrid reference)
    return base * PLI[iso] / PLI_SPAIN
def r0(x): return int(round(x))

rows = []
for city, (iso, region) in sorted(CITY.items()):
    rows.append({
        "city": city, "country": iso, "region": region,
        "ews_archetype_PROVISIONAL": ARCHETYPE[region],
        "PLI": PLI[iso],
        # AC economics (decide product vs installed) 
        "capex_per_user_product":   r0(sc(AC_PRODUCT_2018,  iso) * INFL_AC_2018),
        "capex_per_user_installed": r0(sc(AC_INSTALLED_2018,iso) * INFL_AC_2018),
        "ac_maint_rate": 0.04,          # flat (prEN 15459:2006)
        "ac_lifetime_years": 12,        # flat (Lot-10 Task 4 / 2018 review; UQ range 9-16)
        # trees (decide street vs park context) 
        "capex_per_tree_street": r0(sc(TREE_STREET_2024, iso)),
        "capex_per_tree_park":   r0(sc(TREE_PARK_2024,   iso)),
        "om_per_tree_street":    r0(sc(OM_STREET_2024,   iso)),
        "om_per_tree_park":      r0(sc(OM_PARK_2024,     iso)),
        # EWS opex (Chiabai Madrid x PLI/PLI_Spain; model adds population term itself) 
        "opex_basic":       r0(sc_es(EWS_BASIC_2013,       iso) * INFL_EWS_2013),
        "opex_enhanced":    r0(sc_es(EWS_ENHANCED_2013,    iso) * INFL_EWS_2013),
        "opex_incremental": r0(sc_es(EWS_INCREMENTAL_2013, iso) * INFL_EWS_2013),
        "opex_annual_fixed_if_enabled": r0(sc_es(EWS_FIXED_2013, iso) * INFL_EWS_2013),
        # EWS other (flat, now sourced)
        "pavanello_usd_per_capita_per_day": 0.014,   # Pavanello & Valenti 2025 via Ebi 2004
        "ews_ramp_years": 3, "ews_ramp_initial_efficacy": 0.10,  # Pavanello Fig 4(c)
    })

out = os.path.join(os.path.dirname(__file__), "calibration_params.csv")
cols = list(rows[0].keys())
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
print(f"wrote {out}  ({len(rows)} cities, {len(cols)} columns)")
# quick sanity print for the 4 pilots
for c in ("rome","athens","lisbon","copenhagen"):
    rrow = next(r for r in rows if r["city"]==c)
    print(f"  {c:11s} PLI={rrow['PLI']:5}  capex_user prod={rrow['capex_per_user_product']:4} inst={rrow['capex_per_user_installed']:4}  "
          f"tree street={rrow['capex_per_tree_street']:4} park={rrow['capex_per_tree_park']:3}  "
          f"opex_basic={rrow['opex_basic']:5}")

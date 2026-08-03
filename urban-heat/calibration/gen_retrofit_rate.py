"""
Per-country retrofit_rate_per_year for the 40-city extension, from the EC 2019 renovation study.

Source: European Commission (2019), "Comprehensive study of building energy renovation activities
and the uptake of nearly zero-energy buildings in the EU" (Ipsos/Navigant for DG ENER), Nov 2019.
  - Table 2 = annual residential renovation RATE by depth (below<3% / light 3-30% / medium 30-60% / deep>60%)
  - Table 4 = average primary-energy SAVINGS achieved per renovation, by depth
Both per country, avg 2012-2016. 

Method (exactly the report's own "weighted energy renovation rate" = annual reduction of primary
energy in the stock):
    retrofit_rate = Sum_depth ( rate_depth x savings_depth )
Validation: this reproduces the report's published EU28 figure (~1.0%; our reconstruction = 1.094%).

CAVEAT (flagged decision): the EC savings are PRIMARY ENERGY for heating/DHW/ventilation -- a WINTER
metric. retrofit_rate_per_year is meant to capture SUMMER heat-vulnerability decay. Envelope
improvement helps both, but deep energy retrofits can worsen summer overheating without shading/
ventilation/cooling design. SUMMER_PROXY_DISCOUNT (<=1) lets us down-weight the winter proxy.
Default 1.0 = use the report value as-is; set e.g. 0.7 for a conservative summer discount.
"""
import csv, os

SUMMER_PROXY_DISCOUNT = 1.0   # <<< FLAGGED DECISION (1.0 = report value as-is; <1 discounts the winter->summer proxy)

HERE = os.path.dirname(__file__)
SRC  = os.path.join(HERE, "retrofit_source_ec2019.csv")
PARAMS = os.path.join(HERE, "calibration_params.csv")

# 41 cities -> country ISO (same mapping as gen_calibration_params.py)
CITY = {
 "amsterdam":"NL","athens":"GR","barcelona":"ES","berlin":"DE","bologna":"IT","bratislava":"SK",
 "brussels":"BE","bucharest":"RO","budapest":"HU","cologne":"DE","copenhagen":"DK","dublin":"IE",
 "hamburg":"DE","helsinki":"FI","lisbon":"PT","ljubljana":"SI","lyon":"FR","madrid":"ES",
 "marseille":"FR","milan":"IT","munich":"DE","nantes":"FR","naples":"IT","palermo":"IT","paris":"FR",
 "porto":"PT","prague":"CZ","riga":"LV","rome":"IT","rotterdam":"NL","sevilla":"ES","sofia":"BG",
 "stockholm":"SE","tallinn":"EE","thessaloniki":"GR","varna":"BG","vienna":"AT","vilnius":"LT",
 "warsaw":"PL","zagreb":"HR",
}

# load EC data + recompute the weighted rate (self-check vs stored value)
country = {}
for r in csv.DictReader(open(SRC)):
    iso = r["country_iso"]
    rate = {d: float(r[f"rate_{d}"])/100.0 for d in ("below","light","medium","deep")}
    sav  = {d: float(r[f"sav_{d}"])/100.0  for d in ("below","light","medium","deep")}
    w = sum(rate[d]*sav[d] for d in rate)
    assert abs(w - float(r["weighted_rate_per_year"])) < 1e-4, f"{iso} recompute mismatch"
    country[iso] = w

# per-city retrofit rate 
rows = []
for city, iso in sorted(CITY.items()):
    raw = country[iso]
    rows.append({
        "city": city, "country": iso,
        "retrofit_rate_raw": round(raw, 4),                          # report-faithful (winter PE proxy)
        "retrofit_rate_per_year": round(raw * SUMMER_PROXY_DISCOUNT, 4),  # x flagged summer discount
    })

out = os.path.join(HERE, "retrofit_rate_by_city.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["city","country","retrofit_rate_raw","retrofit_rate_per_year"])
    w.writeheader(); w.writerows(rows)
print(f"summer_proxy_discount = {SUMMER_PROXY_DISCOUNT}")
print(f"wrote {out}  ({len(rows)} cities)")

# merge the two columns into calibration_params.csv 
if os.path.exists(PARAMS):
    prows = list(csv.DictReader(open(PARAMS)))
    by_city = {r["city"]: r for r in rows}
    fields = list(prows[0].keys())
    for c in ("retrofit_rate_raw","retrofit_rate_per_year"):
        if c not in fields: fields.append(c)
    for pr in prows:
        src = by_city.get(pr["city"], {})
        pr["retrofit_rate_raw"] = src.get("retrofit_rate_raw", "")
        pr["retrofit_rate_per_year"] = src.get("retrofit_rate_per_year", "")
    with open(PARAMS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(prows)
    print(f"merged retrofit columns into {PARAMS}")

# validation / comparison to the current config values 
CFG = {"rome":0.011, "athens":0.008, "lisbon":0.012}   # existing per-city config retrofit_rate_per_year
print("\ncity        country  retrofit(report)  config_now")
for c in ("rome","athens","lisbon","copenhagen","warsaw","bucharest","berlin","stockholm"):
    rr = next(r for r in rows if r["city"]==c)
    print(f"  {c:11s} {rr['country']:3s}     {rr['retrofit_rate_per_year']:.4f}          {CFG.get(c,'-')}")

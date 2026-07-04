"""
Per-city electricity_feedback.pct_reduction_per_gvi_point via linear interpolation of the Falchetta (2026) Fig. 5 anchors at each city's summer (JJA) mean daily-MAX temperature.

Source: Falchetta, G. (2026), "Street green space and electricity demand", Energy Economics
158:109311. GVI = Green View Index (greenness). Fig. 5 gives the % reduction in summer AC
electricity per GVI point as a function of maximum temperature. Anchors (verified to match the
paper's Table-2 quadratic coefficients: reduction = 0.0146 - 0.0022*T + 6.61e-5*T^2, floored):
    ANCHORS below. Clamp Tmax to [20, 35] C (floor 0 below 20; cap at 35 anchor above -> no
    extrapolation of the quadratic to hot cities beyond the Italian estimation range).

TEMPERATURE INPUT (city_summer_tmax.csv, column `tmax_jja_c`):
  MUST be JJA monthly-mean daily-MAXIMUM temperature (matching Fig. 5's x-axis; the paper's ten
  cities average 20.2 C). NB: this is NOT the model's internal `citymean_dailymean` metric.
  ** Placeholder mode **: where `tmax_jja_c` is blank, we keep the current flat config value
  (PLACEHOLDER = 0.008 = Falchetta Fig5 @30C) and flag it, to be recomputed once Tmax is filled.
  The repo currently has processed hazard only for the 4 pilots, so the 36 new cities are placeholders until their JJA Tmax is provided.
"""
import csv, os

ANCHORS = [(20.0, 0.000), (25.0, 0.001), (30.0, 0.008), (35.0, 0.020)]  # (Tmax C, reduction/GVI pt)
PLACEHOLDER = 0.008   # current flat config value (Falchetta Fig5 @ ~30C); used where Tmax not yet available
TLO, THI = ANCHORS[0][0], ANCHORS[-1][0]

def interp_clamped(t):
    """Piecewise-linear interpolation of ANCHORS, with Tmax clamped to [TLO, THI]."""
    t = max(TLO, min(THI, float(t)))
    xs = [a for a, _ in ANCHORS]; ys = [b for _, b in ANCHORS]
    for i in range(len(xs) - 1):
        if xs[i] <= t <= xs[i + 1]:
            f = (t - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + f * (ys[i + 1] - ys[i])
    return ys[-1]

HERE = os.path.dirname(__file__)
TMAX = os.path.join(HERE, "city_summer_tmax.csv")
PARAMS = os.path.join(HERE, "calibration_params.csv")

# self-validation against the anchors (must reproduce them exactly) 
for (t, y) in ANCHORS:
    assert abs(interp_clamped(t) - y) < 1e-12, f"anchor {t} mismatch"
assert interp_clamped(18) == 0.000 and interp_clamped(40) == 0.020, "clamp failed"
print("interp validated against anchors + clamp OK")
print("  sanity: 22.5C->%.4f  27.5C->%.4f  32.5C->%.4f" %
      (interp_clamped(22.5), interp_clamped(27.5), interp_clamped(32.5)))

# read per-city Tmax (blank = placeholder) 
tmax = {}
if os.path.exists(TMAX):
    for r in csv.DictReader(open(TMAX)):
        v = (r.get("tmax_jja_c") or "").strip()
        tmax[r["city"]] = float(v) if v not in ("", "NA", "TODO", "nan") else None

# compute per-city value (or placeholder)
rows = []
cities = list(tmax) or []
for city in sorted(cities):
    t = tmax.get(city)
    if t is None:
        rows.append({"city": city, "tmax_jja_c": "", "pct_reduction_per_gvi_point": PLACEHOLDER,
                     "pct_gvi_is_placeholder": "yes"})
    else:
        rows.append({"city": city, "tmax_jja_c": t,
                     "pct_reduction_per_gvi_point": round(interp_clamped(t), 4),
                     "pct_gvi_is_placeholder": "no"})

out = os.path.join(HERE, "pct_gvi_reduction_by_city.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["city", "tmax_jja_c", "pct_reduction_per_gvi_point", "pct_gvi_is_placeholder"])
    w.writeheader(); w.writerows(rows)
n_ph = sum(1 for r in rows if r["pct_gvi_is_placeholder"] == "yes")
print(f"\nwrote {out}  ({len(rows)} cities; {len(rows)-n_ph} computed, {n_ph} PLACEHOLDER)")

# merge into calibration_params.csv 
if os.path.exists(PARAMS) and rows:
    prows = list(csv.DictReader(open(PARAMS)))
    by_city = {r["city"]: r for r in rows}
    fields = list(prows[0].keys())
    for c in ("pct_reduction_per_gvi_point", "pct_gvi_is_placeholder"):
        if c not in fields: fields.append(c)
    for pr in prows:
        s = by_city.get(pr["city"], {})
        pr["pct_reduction_per_gvi_point"] = s.get("pct_reduction_per_gvi_point", PLACEHOLDER)
        pr["pct_gvi_is_placeholder"] = s.get("pct_gvi_is_placeholder", "yes")
    with open(PARAMS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(prows)
    print(f"merged pct_reduction_per_gvi_point (+placeholder flag) into {PARAMS}")

# demonstration: what the pilots WOULD get from their provisional diagnostic Tmax
print("\ndemo (provisional warm-season daily-max from masselot diagnostic; CONFIRM metric before use):")
for city, t in (("rome", 28.19), ("athens", 29.66), ("lisbon", 24.67)):
    print(f"  {city:8s} Tmax~{t}C -> pct_reduction_per_gvi_point = {interp_clamped(t):.4f}")

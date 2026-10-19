# AC electricity use and AC waste heat

Since October 2026 URBADAPT-HEAT computes AC electricity use and the outdoor warming from AC
waste heat with one city-agnostic rule set. This page records what the rules are, where to
change them and how the values were chosen.

## Where the values live

| What | Where |
|---|---|
| Defaults and formulas | `cityheat/ac_heat_energy.py` (`DEFAULTS` dict and module docstring) |
| City overrides | `ac.heat_energy` in `configs/<city>.yml`, using the same keys as `DEFAULTS`; the cooling ramp is also read from `ac.waste_heat.activation` |
| Method switch | `ac.waste_heat.method`: `flux` (default) or `lut` (the earlier Salamanca et al. 2014 lookup, kept for sensitivity runs) |
| Deterministic run | Notebook 05 (sections 3 and 7.2); Notebook 08 reads its outputs |
| Uncertainty runs | `cityheat/nb09_improved_fast.py` samples `WH_SENS` and `AC_KWH_FULL` |
| Values used in a run | `interim/wasteheat_params_<city>.json` (`heat_energy` block) and `interim/ac_per_user_city_<city>.csv` |

To change a value for every city, edit `DEFAULTS`. To change it for one city, set it under
`ac.heat_energy` in that city's config. To apply the framework to a new city nothing needs
to be set: the rules use the city's own hazard, AC users and LCZ raster.

## The rules

**Cooling activity.** On the city-mean daily 2 m temperature *T*,
*a(T)* = clip((*T* − 18 °C) / (25 °C − 18 °C), 0, 1).

**AC electricity.** On a day with *a* > 0 an AC household uses
*e(a)* = 2.5 + 12.5 *a* kWh: 2.5 kWh on the first cooling day, 15 kWh on a day at full
activity. Annual electricity per AC user (person) is the sum of *e(a)* over the year's days,
divided by 2.3 persons per household. It drives the AC operating cost and the waste heat.

**Waste heat.** The units reject electricity × (1 + COP) as sensible heat outdoors, with
COP 3.5. On a day, the AC heat flux averaged over the built-up area (LCZ classes 1–10 of the
city's FUA) is

*Q* = AC users / 2.3 × *e(a)* × 4.5 × 1000 / 24 / *A*<sub>built</sub>  (W m⁻²)

and the city-wide daily-mean warming is Δ*T* = *S* × *Q*, with *S* = 0.02 K per W m⁻².
The waste-heat penalty is the warming on each day times that day's marginal deaths per
degree, summed over the year. That equals the warming at full activity times the
death-weighted mean of *e(a)*/15.

**Uncertainty runs.** *S* is sampled uniformly over 0.01–0.035 K per W m⁻² and the
full-activity electricity over 10–20 kWh per household per day; the central run uses 0.02
and 15.

## How the values were chosen

**Why not the earlier lookup.** Until October 2026 the warming came from Salamanca et al.
(2014, Phoenix), read as household AC penetration → nighttime warming (0.375 / 0.75 / 1.25 °C
at 35 / 65 / 100 %, halved to a daily mean). In that paper those percentages are the share of
floor area that is air-conditioned, at Phoenix's AC loads during a 10-day extreme heat event
(about 8 W m⁻² daily-mean AC heat at 65 %). Applied to European household penetration, the
lookup gave about ten times the warming per W m⁻² of AC heat that the city-scale evidence
supports. For example, the existing AC stock took back 45 % of its own mortality benefit,
against about 8 % in the only study that carries AC heat through to deaths (Chua et al. 2023,
Kansai cities, 96–98 % AC ownership).

**Sensitivity *S*.** City-scale modelling studies give a median of 0.017 K per W m⁻² of
anthropogenic heat flux (interquartile range 0.008–0.029; summer nights about 0.04, summer days
about 0.012; Li et al. 2026 synthesis of 81 estimates, preprint). The other anchors:
- the observed weekday–weekend contrast in Tokyo and Osaka, 0.007–0.009 (Kikegawa et al. 2014);
- Paris, 0.005 per W m⁻² of peak flux (Tréméac et al. 2012; de Munck et al. 2013);
- Berlin, about 0.01 (Jin et al. 2020);
- ten central-European cities, 0.006–0.007 (Karlický et al. 2026);
- the continental US, about 0.01 (Wang et al. 2023);
- Phoenix, about 0.035 (derived from Salamanca et al. 2014).

The central 0.02 sits at the upper end of the central estimates; it was chosen because
European cities are compact. The sampled range runs from the US-wide value to the Phoenix value.

**Electricity per day.** 2.5–15 kWh per AC household per cooling day spans a single unit
running a few hours at the onset to several hours of full-load operation of a ~1 kW (electric)
split unit on a very hot day. In the cooler cities the annual totals it implies (about
100–300 kWh per AC household) match national statistics (JRC-IDEES, Eurostat). In the hottest
cities it gives more, at least 1,300 kWh per AC household, against 230–490 kWh for Italy and
about 250 kWh for Spain. So in the south it is an upper envelope, and the waste heat
and electricity cost it implies err on the high side. It replaced a NUTS-level kWh-per-user
table whose values did not follow climate (for example 38 kWh in Lyon, 591 kWh in Dublin).

**Other constants.** Persons per household 2.3 is the EU-27 mean. COP 3.5 is the seasonal
efficiency of the installed split-unit stock (Ecodesign review 2018: 2.9–3.2 for the stock;
JRC-IDEES: 3.5–4.8). All heat is taken as sensible, which suits split units; evaporative
towers would release most of it as latent heat.

## References

- Chua et al. 2023, Environment International 181:108310, doi:10.1016/j.envint.2023.108310
- de Munck et al. 2013, International Journal of Climatology 33:210–227, doi:10.1002/joc.3415
- Jin et al. 2020, IJERPH 17:4645, doi:10.3390/ijerph17134645
- Karlický, Bareš & Huszár 2026, Environmental Research: Climate 5:015007, doi:10.1088/2752-5295/ae228c
- Kikegawa et al. 2014, Theoretical and Applied Climatology 117:175–193, doi:10.1007/s00704-013-0996-8
- Li et al. 2026, arXiv:2608.17782 (preprint; data at doi:10.5281/zenodo.21125534)
- Salamanca et al. 2014, JGR Atmospheres 119:5949–5965, doi:10.1002/2013JD021225
- Tréméac et al. 2012, Applied Energy 95:102–110, doi:10.1016/j.apenergy.2012.02.015
- Wang et al. 2023, Environmental Research Letters 18:094005, doi:10.1088/1748-9326/ace7e0

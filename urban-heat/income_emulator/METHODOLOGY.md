# Within-city income distribution emulator — methodology

## 1. Problem and objective

URBADAPT-HEAT has observed within-city income for a handful of cities at heterogeneous
sub-city units (Rome and Genova at postal-code *CAP* zones from IRPEF tax records; Athens
as an income raster; Copenhagen at districts; Lisbon at *freguesias*). The AC-downscaling
step (`05_AC_*.ipynb`) needs, for *every* city it models (including the ~356 cities in
`gvi_356_cities.csv` that have **no** income data), a measure of how income is distributed
across sub-city zones.

The objective is an **emulator** that, trained on the labelled cities, predicts that
within-city distribution for unlabelled cities using only covariates that exist across
Europe at fine spatial resolution: the Global Human Settlement Layer (GHSL) and the
Eurostat/GISCO 2021 population-and-housing census grid, plus a few derived contextual
layers the project already produces.

## 2. What we predict: a scale-free relative index

We deliberately do **not** model absolute income. Absolute values are not comparable
across the training cities: they mix currencies, years, and definitions (IRPEF *reddito
complessivo* per taxpayer in the Italian cities, per-capita income elsewhere), so a model
trained to predict euros in Rome would not transfer to Lisbon.

Instead we model a **relative income index** for each zone *i* in city *c*:

> RII(c,i) = income(c,i) / reference(c)

where `reference(c)` is the population-weighted mean income of city *c* (median and a
caller-supplied municipal value are also supported). By construction the index equals
**1.0 when a zone sits exactly at the municipal mean**, and varies above and below it,
which is precisely the quantity requested. We fit on the natural log of the ratio,
`ln(income/reference)`, because income ratios are right-skewed and the log makes
over- and under-representation symmetric; predictions are exponentiated back to the index.

For drop-in compatibility with the existing AC machinery, the pipeline also emits
**`p_inc`**, the population-weighted income percentile rank (0–1, poorer zones lower),
computed from the predicted index with the same midpoint-cumulative-share convention used
in `05_AC_*.ipynb`. So an unlabelled city can run the current income-ranked AC sigmoid
unchanged.

## 3. Predictors

All predictors are zonal-aggregated to each sub-city zone. In URBADAPT-HEAT the relevant
rasters are already reprojected to a common reference grid by
`cityheat/vulnerability_layer.py` (`reproject_to_ref`, using *sum* resampling for extensive
census counts and *nearest* for categorical GHS classes), so the emulator can read those
arrays rather than re-deriving them.

GHSL R2023A (100 m, ETRS89-LAEA / EPSG:3035; SMOD at 1 km):
- **GHS-POP** → population and population density.
- **GHS-BUILT-S** → built-up surface fraction.
- **GHS-BUILT-V** → built volume per capita (a living-space / dwelling-size proxy that
  tends to rise with income, though it also flags non-residential cores).
- **GHS-BUILT-H** → mean building height (high-rise vs low-rise morphology).
- **GHS-AGE** (already used for the thermal-justice layer) → mean built-environment age
  class; older stock often signals different income profiles.
- **GHS-SMOD** → degree-of-urbanisation class shares (urban centre / cluster / rural).

GISCO 2021 census grid (1 km, EPSG:3035; the `ESTAT_OBS-VALUE-*_2021` rasters the project
already downloads):
- **share 65+ and under-15** (age bands from `OBS-VALUE-T`).
- **employment rate** = employed (`EMP`) / working-age (`Y_1564`).
- **foreign-born share** = `OTH` / total (`T`), a place-of-birth proxy that correlates
  with income across European cities.

Contextual / project layers:
- **green-view index** (`gvi_356_cities.csv`) as an amenity proxy.
- **distance to city centre**, capturing the monocentric income gradient.
- optional **night-time lights per capita** (VIIRS) where available.

### 3.1 The key transform: express predictors *within* each city

Each predictor is converted to its **within-city** form — a z-score against the city mean
(default) or a ratio to it — before entering the model. This removes city-level scale,
currency, and year effects and forces a single pooled model to learn the transferable
mapping *"deviation of local built form / demography → deviation of local income"* rather
than memorising city averages. A small set of raw city-level context features (e.g. total
municipal population, country) is kept un-transformed to allow conditional effects.

## 4. Model

Gradient-boosted regression trees (LightGBM preferred; scikit-learn
`GradientBoostingRegressor`/`RandomForest` as alternatives) on the log-ratio target. Trees
capture the non-linear, interacting relationships (e.g. high built volume per capita only
signals wealth away from the commercial core) that a linear model misses. Observations are
weighted by zone population so the fit prioritises where people actually live. The codebase
also ships a dependency-free closed-form **ridge** fallback so the pipeline runs and
self-tests with only NumPy/pandas; it is adequate for smoke tests but trees are recommended
for production.

This follows the established literature on inferring fine-scale socioeconomic status from
remotely-sensed and built-form proxies with spatially cross-validated machine learning
(e.g. Chi et al., *PNAS Nexus* 2025, on mapping fine-scale inequality).

## 5. Validation — honest about the deployment scenario

Because the emulator will be applied to cities with **no** income data, the only honest
test holds out **whole cities** (or whole countries), never individual zones. Random
zone-level cross-validation would leak the city's income level through the within-city
normalisation and grossly overstate skill. The pipeline therefore defaults to
**leave-one-city-out** cross-validation (leave-one-country-out and grouped k-fold are also
provided).

Headline metric is the **per-city Spearman rank correlation** between observed and
predicted index: does the emulator order rich versus poor zones correctly? That ordering is
exactly what the downstream AC sigmoid consumes. We also report a population-weighted mean
absolute error on the index. On synthetic data with a shared latent rule the harness
recovers ordering well (leave-one-city-out Spearman ≈ 0.89); the real-data number will be
the empirical test of whether five training cities transfer.

## 6. Post-processing

Predicted indices are renormalised so their population-weighted mean is exactly 1.0 within
each city (internal consistency with the index definition). If a city's absolute municipal
mean income is known, multiplying the index by it recovers an absolute estimate; otherwise
the index and `p_inc` are used directly.

## 7. Limitations and caveats

- **Small training set.** Five cities is thin for learning a transferable rule; expect wide
  per-city CV variance. Adding cities (Eurostat Urban Audit sub-city districts, or national
  open income data at IRIS/LAU level) is the single highest-value improvement.
- **Definitional heterogeneity.** Taxable income per taxpayer (Italy) is not the same
  construct as per-capita disposable income. The relative index mitigates level differences
  but not differences in the *shape* of the within-city distribution across definitions.
- **Selection bias.** Cities that publish sub-city income may not represent those that do
  not; transfer skill measured on the five may be optimistic.
- **MAUP and ecological inference.** Zones differ in size and internal heterogeneity;
  predictions are zone averages, not household-level statements.
- **Temporal mismatch.** Census 2021 / GHSL epochs may not align with the income reference
  year; treat the index as a structural, slowly-varying pattern.
- **Causal silence.** Predictors are proxies. The emulator estimates spatial *association*
  for downscaling, not the determinants of income.

## 8. How this plugs into URBADAPT-HEAT

1. Build the zone predictor table per city from the already-reprojected GHS/census arrays
   (or `pipeline/zonal.py` for a standalone path), one row per zone, with `inc_mean` left
   blank for unlabelled cities.
2. Run the emulator (`run.py`) to get `income_index_pred` and `p_inc` for every zone.
3. Feed `p_inc` into the existing `05_AC` income-ranked AC sigmoid for cities that lacked
   income, so AC penetration, heat-mortality, and equity analyses can be produced for the
   full city set rather than only the five with observed income.

## Sources

- GHSL R2023A products and resolutions: [GHSL Datasets](https://human-settlement.emergency.copernicus.eu/datasets.php), [GHS P2023 release](https://human-settlement.emergency.copernicus.eu/p2023Release.php)
- GISCO 2021 census grids (variables, 1 km INSPIRE grid, EPSG:3035, formats): [Eurostat — Population and housing census 2021 population grids](https://ec.europa.eu/eurostat/statistics-explained/index.php?title=Population_and_housing_census_2021_-_population_grids), [GISCO population grids](https://ec.europa.eu/eurostat/web/gisco/geodata/population-distribution/population-grids)
- Eurostat Urban Audit sub-city districts (candidate extra training labels): [City statistics (urb) metadata](https://ec.europa.eu/eurostat/cache/metadata/en/urb_esms.htm)
- Fine-scale socioeconomic mapping with ML + remote sensing: [Chi et al., PNAS Nexus 2025](https://academic.oup.com/pnasnexus/article/4/2/pgaf040/8005621)

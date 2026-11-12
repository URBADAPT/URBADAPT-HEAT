# Street-tree cooling coefficients

URBADAPT-HEAT reads the street-greenery cooling coefficients from
`data/<City>/CoolingEff/coefs_GVI_lst.csv` (columns `city, lcz, month, month_name, coef, se, t_stat`):
for each city, Local Climate Zone and calendar month, the change in daily maximum land-surface
temperature (LST) per Green View Index point, with its standard error, for the coefficients
significant at |t| >= 1.96. The same file is used by every city (SHA-256 `0ba9ece06b0fe6e3...`,
139 cities, 3,981 rows); it is included in the input record of the paper. The vegetation notebook
(NB07) clips positive coefficients to zero and translates the LST change into air temperature with the
LST-T2M emulator (`../lst_t2m_emulator/`).

The coefficients were estimated with the code of the URGED project (Falchetta et al., 2026,
*Environmental Research Letters*, doi:10.1088/1748-9326/ae5c20; https://github.com/giacfalk/URGED;
replication data doi:10.5281/zenodo.17803495).

## Scripts

`LST_estimation/` contains the LST version of the URGED estimation:

- `estimate_gvi_coefs_LST_panel_multicities.R`: builds the city panels of daily maximum LST
  (UrbClim, 2008-2017) with Green View Index and urban-form covariates, and fits the city x LCZ x
  month panel regressions of LST on GVI, with standard errors clustered by grid point;
- `run_LST_max_all_cities_raw_gvi.R`: runs the estimation for all cities with GVI on the 0-100 scale
  and writes the full coefficient table and the table of significant coefficients, which it also
  writes as `coefs_GVI_lst.csv`.

## Relation to the table used by the model

`coefs_GVI_lst.csv` was produced by an earlier run of this estimation, made before March 2026; the
exact code version of that run was not preserved. These scripts are the versions of 28 August and
1 September 2026. Their run of 28 August reproduces the coefficient point estimates of the table used
by the model: of the 3,589 city x LCZ x month estimates the two have in common, 3,450 (96 %) agree
within 1 % and 1,357 are identical, and the estimates that differ by more than 1 % are confined to six
cities (Bari, Dubai, Dublin, Dusseldorf, Edinburgh and Prague; none of the four pilot cities). The
standard errors differ (median ratio between the two shown per city), so a different subset of
estimates passes the significance filter, and 12 cities of the model table could not be rerun because
their input data were unavailable. `LST_estimation/comparison_with_model_input.csv` gives the
comparison city by city.

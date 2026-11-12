# Street-tree cooling coefficients

URBADAPT-HEAT reads the street-greenery cooling coefficients from
`data/<City>/CoolingEff/coefs_GVI_lst.csv` (columns `city, lcz, month, month_name, coef, se, t_stat`):
for each city, Local Climate Zone and calendar month, the temperature change per unit of Green View
Index, with its standard error. The same file is used by every city (SHA-256 `0ba9ece06b0fe6e3...`);
it is included in the input record of the paper.

The coefficients were estimated with the code of the URGED project, which accompanies

> Falchetta, G., et al. (2026). Street green space is relevant but not sufficient for adapting to
> growing urban heat in world cities. *Environmental Research Letters*, doi:10.1088/1748-9326/ae5c20.

`URGED_53c6610/` contains the scripts of that project that estimate the coefficients, at commit
`53c6610` (2026-05-11) of https://github.com/giacfalk/URGED:

- `estimate_gvi_coefs_<metric>_panel_multicities.R`: builds the city panels and fits the city x LCZ x
  month panel regressions of temperature on Green View Index;
- `run_<metric>_all_cities.R`: runs the estimation for all cities and writes the coefficient tables.

`coefs_GVI_lst.csv` is the coefficient table written by these scripts, renamed when it was added to
the URBADAPT-HEAT inputs. The rest of the URGED code (data preparation, projections, figures) is in
the repository above, and its replication data are archived at doi:10.5281/zenodo.17803495
(CC BY 4.0). Within URBADAPT-HEAT, the vegetation notebook (NB07) clips positive coefficients to zero.

# visual_items

Figures, tables and every quoted number derived from URBADAPT-HEAT model outputs, in two
parts. Nothing here runs the model; each script reads the output trees the pipeline writes
(`urban-heat/outputs_variants/<variant>/<city>/...`) and the NB09 uncertainty campaigns.

| Folder | Scope | Entry points |
|---|---|---|
| `multi_city/` | 40 European cities: risk, cost-effectiveness, portfolios, distribution, synergies, uncertainty | `R/build_all.R`; `results_numbers.py`, `ews_numbers.py`, `build_uncertainty_table.py` |
| `pilot_cities/` | Four pilot cities (Rome, Athens, Lisbon, Copenhagen): framework figures and uncertainty | `workflow_figures/make_figures.py`, `workflow_tables/make_uncertainty_matrix.py`, `uncertainty/build_fig_uncertainty.py`, `uncertainty/build_n_convergence.py` |

Rendered figures and tables are written beside the scripts and ignored by git; rebuild them
rather than editing them. `pilot_cities/figure_manifest.csv` maps each pilot-city figure to
the script or notebook that produces it; `multi_city/README.md` documents the 40-city
build in detail.

## Inputs

**multi_city** (environment variables; the build prints the resolved tree and city count):

- `MULTICITY_OUTPUTS_BASE` - the 40-city output tree (`<city>/tables`, root files).
- `MULTICITY_AUX_BASE` - the `interim/` companion of the same run, when the main tree is slimmed.
- `MULTICITY_FIG_DIR`, `MULTICITY_TAB_DIR` - output folders (default `multi_city/figures`, `multi_city/tables`).
- `PAWN_TREE`, `PAWN_CAMPAIGN` - the NB09 campaigns behind the PAWN sensitivity figure (`R/si4_pawn.R`).

**pilot_cities**: the output tree is found from the repository layout (`--variant` selects it);
the uncertainty scripts take `--tree <variant dir>` (or a packaged `--archive`) and select
campaigns by pattern, counting only completed ones.

## Running

- R: run under PowerShell (`Rscript build_all.R` from `multi_city/R`); `ncdf4`/`terra` crash
  under Git Bash's `Rscript` on Windows.
- Python: `workflow_figures` needs the `urbanheat` conda environment (geopandas, netCDF4);
  the other scripts need only numpy, pandas and matplotlib.

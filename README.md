<div align="center">

  <h1>URBADAPT-HEAT</h1>

  <p><strong>A scalable geospatial framework for city-level urban heat risk assessment<br>and public–private adaptation cost-benefit analysis</strong></p>

  <p>
    <a href="https://urbadapt.github.io"><img src="https://img.shields.io/badge/website-urbadapt.github.io-1f6feb.svg" alt="Website"></a>
    <a href="https://github.com/URBADAPT/URBADAPT-HEAT/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-CC0%201.0-blue.svg" alt="License: CC0 1.0"></a>
    <img src="https://img.shields.io/badge/python-3.10-blue.svg" alt="Python 3.10">
    <img src="https://img.shields.io/badge/CLIMADA-6.1.0-green.svg" alt="CLIMADA 6.1.0">
    <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey.svg" alt="Platform">
  </p>

  <p>
    <a href="https://urbadapt.github.io">🌐 Website</a> ·
    <a href="https://github.com/URBADAPT/URBADAPT-HEAT/wiki">📖 Wiki</a> ·
    <a href="#installation">⚙️ Installation</a> ·
    <a href="#quick-start">🚀 Quick start</a> ·
    <a href="#citation">📄 Citation</a>
  </p>
</div>

---

## Overview

**URBADAPT-HEAT v1.0** is the heat-specific implementation of the URBADAPT modular framework for urban climate risk assessment and adaptation planning. Built on the [CLIMADA](https://github.com/CLIMADA-project/climada_python) probabilistic risk engine (v6.1.0), it provides a fully reproducible, city-agnostic pipeline that:

- **Maps urban heat hazard** at ~100 m resolution from the [UrbClim](https://www.vito.be/en/urbclim) high-resolution urban climate dataset, for a 2020 synthetic baseline and climate projections to 2050 under four CMIP6 scenarios.
- **Quantifies heat-attributable mortality** using age-stratified population exposure ([WorldPop](https://www.worldpop.org/) + [Wittgenstein Centre](https://www.oeaw.ac.at/vid/data-and-information-systems/wittgenstein-centre-data-explorer) demographics) and epidemiologically calibrated impact functions ([Burke et al. 2025](https://doi.org/10.1038/s41591-024-03094-4)).
- **Evaluates three adaptation pathways** — air conditioning (AC), urban street trees, and early warning systems (EWS) — through their physical mechanisms (hazard modification, impact-function attenuation, event-specific mortality reduction), explicitly representing cross-pathway interactions.
- **Integrates a 25-year discounted cost-benefit analysis** including externalities (AC waste heat), co-benefits (vegetation → reduced AC electricity demand), and distributional outcomes stratified by a composite Social Vulnerability Index.
- **Identifies cost-effective adaptation portfolios** via Pareto-frontier budget optimisation.

Configuration is fully externalised to city-specific YAML files — the same analytical code runs unchanged across all cities.

<div align="center">
<img src="https://github.com/URBADAPT/URBADAPT-HEAT/blob/main/diagram.png?raw=true" alt="URBADAPT-HEAT workflow diagram" width="80%">
</div>

---

## Repository structure

```text
URBADAPT-HEAT/
├── logo_urbadapt.png
├── LICENSE                          # CC0 1.0
└── urban-heat/
    ├── environment.yml              # Conda environment (Python 3.10, conda-forge)
    ├── pyproject.toml               # cityheat package metadata
    ├── launch_windows.bat           # One-click Windows launcher
    ├── cityheat/                    # Python helper package
    │   ├── config.py                # YAML config loader/validator
    │   ├── data_io.py               # Raster, NetCDF, geodata I/O
    │   ├── grids.py                 # Reference grid, reprojection, FUA mask
    │   ├── hazards.py               # UrbClim T2M processing, tree-cooling scaling
    │   ├── impacts.py               # Age-varying impact functions, deaths with AC
    │   ├── benefits.py              # Event-to-annual interpolation, discounting, PV
    │   ├── costs.py                 # AC CAPEX/maintenance/electricity PV calculators
    │   ├── ac_downscale.py          # Logistic income-rank AC coverage downscaling
    │   ├── trees.py                 # ΔGVI → cost mapping, ramp years, O&M/CAPEX split
    │   ├── mix.py                   # Budget grid-search, Pareto-optimal policy mixes
    │   ├── vulnerability_layer.py   # Static and dynamic SVI construction
    │   ├── nb09_improved.py         # Monte Carlo uncertainty quantification (PAWN)
    │   ├── nb10_summary.py          # Summary statistics and reporting
    │   ├── plotting.py              # Standard maps and result figures
    │   └── run_city.py              # Full-city pipeline orchestrator
    ├── configs/                     # City-specific YAML configuration files
    │   ├── rome.yml
    │   ├── athens.yml
    │   ├── lisbon.yml
    │   ├── copenhagen.yml
    │   ├── genova.yml
    │   └── barcelona.yml
    ├── data_manifests/              # Google Drive sync manifests (per city)
    ├── scripts/                     # Standalone preprocessing scripts
    │   ├── build_delta_bands.py     # Build CMIP6 climate-uncertainty delta CSV
    │   ├── build_dynamic_exposures.py
    │   ├── build_projected_vulnerability.py
    │   ├── download_drmkc_vulnerability.py
    │   └── prepare_gvi_projections.py
    ├── notebooks/
    │   ├── 00_run_city.ipynb        # Driver notebook (calls run_city.py)
    │   └── city_agnostic/
    │       └── January2026/         # Canonical city-agnostic pipeline
    │           ├── 01_setup_0126.ipynb
    │           ├── 02_grids_0126.ipynb
    │           ├── 03_hazard_exposure_0126.ipynb
    │           ├── 04_impact_functions_0126.ipynb
    │           ├── 05_AC_0126.ipynb
    │           ├── 06_EWS_0126.ipynb
    │           ├── 07_vegetation_0126.ipynb
    │           └── 08_CBA_0126.ipynb
    │       └── <City>/March2026/    # City-specific runs with uncertainty (NB09, NB10)
    ├── data/                        # Input data (gitignored; synced from Drive)
    └── outputs/                     # Model outputs (gitignored)
```

---

## Installation

### Prerequisites

- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda
- Git

### Step-by-step

**1. Clone the repository**

```bash
git clone https://github.com/URBADAPT/URBADAPT-HEAT.git
cd URBADAPT-HEAT
```

**2. Create and activate the conda environment**

```bash
conda env create -f urban-heat/environment.yml
conda activate urbanheat
```

All packages install from the `conda-forge` channel. Key pinned dependencies: Python 3.10 · CLIMADA 6.1.0 · rasterio 1.4.3 · geopandas 1.1.1 · xarray 2025.6.1.

**3. Install the `cityheat` helper package**

```bash
cd urban-heat
pip install -e .
```

**4. Register the Jupyter kernel**

```bash
python -m ipykernel install --user --name urbanheat --display-name "Python (urbanheat)"
```

**5. Launch JupyterLab**

```bash
jupyter-lab
```

> **Windows shortcut:** edit `PROJECT_DIR` in `urban-heat/launch_windows.bat` and double-click. It handles conda activation, environment creation, kernel registration, and JupyterLab launch automatically.

---

## Data access

Input data are **not included in the repository** (size). They live in three places:

1. **Google Drive** — most static inputs (FUA, LCZ, vulnerability rasters, AC tables, T2M climate deltas, emulator bundle, etc.) are stored on Drive and synced by `urban-heat/notebooks/.../01_setup_*.ipynb` via city-specific manifests in `urban-heat/data_manifests/` using `gdown`. Each manifest entry has an optional `base_kind` field: `"data"` (default) writes under `urban-heat/data/<city>/`; `"outputs"` writes under `urban-heat/outputs/<city>/` (or `urban-heat/outputs_variants/<variant>/<city>/` when an output variant is active) — used for the city-agnostic emulator bundle which NB07 expects under `OUT`.
2. **PROVIDE/VITO API** — UrbClim daily-mean T2M NetCDFs are fetched directly during `01_setup_*.ipynb`. Endpoints and city identifiers are specified per city in `urban-heat/configs/<city>.yml` under `climate.urbclim_api`.
3. **WorldPop R2025A age-structures API** — 100 m age-stratified population rasters are fetched directly from the [WorldPop hub REST API](https://hub.worldpop.org/rest/data/age_structures) during NB02, using the `WP_ISO3` country code and `WP_YEARS` declared in the city's YAML config. NB02 lands the files under `urban-heat/data/<city>/worldpop/<ISO3>/<year>/` and re-uses them on subsequent runs. No manual download is required.

### Reproducibility of generated inputs

Three input tables are produced by helper scripts in `urban-heat/scripts/`. The script outputs are mirrored to Drive for convenience (so a fresh clone + Drive sync gives a working pipeline out of the box), but the scripts themselves remain the source of truth for refreshing them:

**1. `scripts/build_delta_bands.py`** — produces `T2MmeanDeltas/climate_change_provide_markups_bands.csv` and `..._gcm.csv` from the PROVIDE per-GCM delta files (the input root defaults to a sibling project directory; override with `--deltas-root`):

```bash
python scripts/build_delta_bands.py \
    --cities Rome Athens Lisbon Copenhagen \
    --scenarios CurPol GS SP ssp585 \
    --gcm-low-pct 25 --gcm-high-pct 75
```

**2. `scripts/download_drmkc_vulnerability.py`** — produces `vulnerability/drmkc_vulnerability_projection.csv` from the JRC Risk Data Hub API. **Requires a personal JWT token** obtained manually from the [Risk Data Hub user info page](https://drmkc.jrc.ec.europa.eu/risk-data-hub-api/risk-data-hub-ui/userinfo):

```bash
export RDH_API_TOKEN='your-jwt-token-here'
python scripts/download_drmkc_vulnerability.py \
    --country-code IT --country-code GR --country-code PT --country-code DK \
    --output-dir urban-heat/data/Rome/vulnerability/
```

**3. `scripts/prepare_gvi_projections.py`** — produces `vulnerability/gvi_projections.csv` from the [Huisman et al. (2025) SSP-GVI](https://doi.org/10.1038/s41597-024-04150-x) source CSV (download separately from the Huisman et al. Scientific Data paper):

```bash
python scripts/prepare_gvi_projections.py \
    --input /path/to/huisman_2025_ssp_gvi.csv \
    --iso3 ITA --iso3 GRC --iso3 PRT --iso3 DNK \
    --output urban-heat/data/Rome/vulnerability/gvi_projections.csv
```

A new user cloning the repo does **not** need to run these three scripts — NB01's Drive sync covers the standard 4-city pilot. Run them only when refreshing the underlying data sources (new GCM run, updated RDH snapshot, newer SSP-GVI release) or when adding a new city outside the pilot four.

---

## Quick start

Once the environment is set up and data are in place, open the notebooks in order and point each to your city YAML at the top of the cell:

| # | Notebook | What it does |
|---|---|---|
| 01 | `01_setup_0126.ipynb` | Load config, resolve paths, sync data |
| 02 | `02_grids_0126.ipynb` | Build reference grid and FUA mask |
| 03 | `03_hazard_exposure_0126.ipynb` | Build daily T2M hazard; map population exposure |
| 04 | `04_impact_functions_0126.ipynb` | Age-specific heat-mortality impact functions; baseline deaths |
| 05 | `05_AC_0126.ipynb` | AC penetration downscaling; policy scenarios; waste-heat feedback |
| 06 | `06_EWS_0126.ipynb` | Early warning system; warning days; avoided deaths and costs |
| 07 | `07_vegetation_0126.ipynb` | GVI → LST → T2M cooling; tree-planting allocation and costs |
| 08 | `08_CBA_0126.ipynb` | 25-year CBA; Pareto optimisation; equity stratification |
| 09 | `09_uncertainty_*.ipynb` | Monte Carlo uncertainty; PAWN global sensitivity |
| 10 | `10_summary_*.ipynb` | Aggregated results tables and figures |

All city-specific parameters (NUTS3 identifiers, AC penetration rates, vulnerability projection parameters, tree costs, EWS thresholds, etc.) are controlled by the YAML config in `urban-heat/configs/`. See the [City Configuration wiki page](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/City-Configuration) for the full parameter reference.

---

## Documentation

Full documentation is available on the **[URBADAPT website](https://urbadapt.github.io/urbadapt-heat/)** —
a browsable, searchable rendering of the
[project wiki](https://github.com/URBADAPT/URBADAPT-HEAT/wiki), which remains the
source these pages are written in and where edits should be made:

| Page | Description |
|---|---|
| [Installation & Usage](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Installation) | Detailed setup, data access, pipeline walkthrough |
| [Framework Overview](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Framework-Overview) | Architecture, design principles, module map |
| [Hazard](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Hazard) | UrbClim T2M, synthetic baseline, CMIP6 delta scaling |
| [Exposure](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Exposure) | Age-stratified population, WorldPop, Wittgenstein Centre |
| [Vulnerability](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Vulnerability) | Composite SVI, dynamic projection, city parameters |
| [Impact Functions](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Impact-Functions) | Burke (2025) calibration, dose-response equations |
| [Adaptation Pathways](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Adaptation-Pathways) | AC, street trees, EWS, cross-pathway interactions |
| [Cost-Benefit Analysis](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Cost-Benefit-Analysis) | 25-year CBA, budget optimisation, equity outputs |
| [Uncertainty Analysis](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Uncertainty-Analysis) | Structural, climate, parametric sensitivity (PAWN) |
| [City Configuration](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/City-Configuration) | YAML reference, adding a new city |
| [Case Studies](https://github.com/URBADAPT/URBADAPT-HEAT/wiki/Case-Studies) | Rome · Athens · Lisbon · Copenhagen results |

---

## Citation

If you use URBADAPT-HEAT in your research, please cite:

> Aboudrar-Méda, A. & Falchetta, G. (2026). *URBADAPT-HEAT v1.0: a scalable geospatial framework for city-level public-private adaptation infrastructure cost-benefit analysis and its urban heat risk implementation*. In preparation.

```bibtex
@article{aboudrar_falchetta_2025_urbadaptheat,
  author  = {Aboudrar-M{\'e}da, Armande and Falchetta, Giacomo},
  title   = {{URBADAPT-HEAT} v1.0: a scalable geospatial framework for city-level
             public-private adaptation infrastructure cost-benefit analysis
             and its urban heat risk implementation},
  journal = {In preparation},
  year    = {2026}
}
```

## License

This repository is released under the **Creative Commons Zero v1.0 Universal (CC0 1.0)** public domain dedication. See [LICENSE](LICENSE) for details.

---

## Authors and contact

| Name | Affiliation | Contact |
|---|---|---|
| **Armande Aboudrar-Méda** | CMCC | armande.aboudrar-meda@cmcc.it |
| **Giacomo Falchetta** | CMCC, IIASA | giacomo.falchetta@cmcc.it |

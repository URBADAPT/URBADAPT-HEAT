# The socio-economic implications of public-private infrastructure interfaces in city-scale adaptation

Building a **generalisable pipeline** to study climate hazards and compare infrastructure adaptation strategies across cities. 

- Rome as a case-study, first example, not the end goal

- For a pool of European cities, how do different hazards (starting here with heat) translate into risk? 

- Which infrastructure adaptations (AC uptake, cool roofs, shade/trees, cooling centers…) for which criteria ? Ultimately: multi-criteria optimisation (minimisation of costs, maximisation of benefits, targeting the most vulnerable in priority…)

- How to make this repeatable and comparable across cities with minimum manual work? 

**Framework**: using as much as possible data that exist for different cities to build a reproducible pipeline. Ultimately designed to swap in new hazards, cities, and adaptation options with minimal refactoring. 

---

## Diagram 

[![Diagram preview](urban-heat/notebooks/city_agnostic/January2026/diagram.png)](urban-heat/notebooks/city_agnostic/January2026/diagram.png)

**Open interactive version:** https://github.com/giacfalk/URBADAPT/blob/main/urban-heat/notebooks/city_agnostic/January2026/diagram_0126.html
**HTML source in repo:** URBADAPT/urban-heat/notebooks/city_agnostic/January2026/diagram_0126.html

---

## Repository Structure

```text
URBADAPT/
└── urban_heat/
    ├── cityheat/                                  
    │   ├── __init__.py                              # Expose public API 
    │   ├── config.py                                # Read/validate YAML city configs
    │   ├── data_io.py                               # Raster/NetCDF/Geo data loaders + writers
    │   ├── grids.py                                 # Reference grid, reprojection, masks
    │   ├── hazards.py                               # UrbClim WBGT + tree-cooling scaling (f_ref)
    │   ├── impacts.py                               # Impact functions; deaths_with_ac (age-varying)
    │   ├── benefits.py                              # Event to annual interpolation, discounting, PV
    │   ├── costs.py                                 # AC capex/maint/electricity PV calculators
    │   ├── ac_downscale.py                          # Build baseline/policy AC coverage, kWh/user
    │   ├── trees.py                                 # ΔGVI to€ mapping, ramp years, O&M/CAPEX split
    │   ├── mix.py                                   # Budget grid-search; trees/AC mixes
    │   ├── plotting.py                              # Standard maps/figures
    │   └── run_city.py                              # Organises a full city run
    │   └── vulnerability_layer.py                   # Creates SVI based on thermal age of buildings, unemployment shares and people born out of EU 
    │
    ├── configs/                                     # One YAML per city 
    │   ├── rome.yml
    │   └── barcelona.yml
    │   └── genova.yml    
    ├── data/                                        # From Drive Synch and Vito API 
    │   └── Rome/
    │   └── Genova/
    │   
    ├── notebooks/
    │   ├── 00_run_city.ipynb                        # Thin driver notebook (calls run_city)
    │   └── City Agnostic/
    │       └── January2026/
    │           ├── 01_setup_0126.ipynb                     # Env + config load, paths
    │           ├── 02_grids_0126.ipynb                     # Ref grid + masks
    │           ├── 03_hazard_exposure_0126.ipynb           # WBGT events; exposure
    │           ├── 04_impact_functions_0126.ipynb          # Age differentiated IF; per-pixel deaths
    │           ├── 05_AC_0126.ipynb                        # AC policy; downscaling AC penetration and AC electricity consumption 
    │           ├── 06_EWS_0126.ipynb                       # EWS policy
    │           ├── 07_vegetation_0126.ipynb                # Vegetation policy 
    │           ├── 08_CBA_0126.ipynb                       # Trees EAC/PV; AC CAPEX/maint/electricity PV; O&M vs CAPEX; benefits/costs; €/avoided; Budget-constrained mixes 
    │       └── Rome/ 
    │           ├── 01_setup_*.ipynb                     # Env + config load, paths
    │           ├── 02_grids_*.ipynb                     # Ref grid + masks
    │           ├── 03_hazard_exposure_*.ipynb           # WBGT events; exposure
    │           ├── 04_impact_functions_*.ipynb          # Age differentiated IF; per-pixel deaths
    │           ├── 05_AC_*.ipynb                        # AC policy; downscaling AC penetration 
    │           ├── 06_vegetation_*.ipynb                # Vegetation/Tree policy
    │           ├── 07_downscale_electricity_*.ipynb     # Downscaling AC electricity 
    │           ├── 08_CBA_*.ipynb                       # Trees EAC/PV; AC CAPEX/maint/electricity PV; O&M vs CAPEX; benefits/costs; €/avoided; Budget-constrained mixes 
    │           
    │       └── Genova/
    │           ├── 01_setup_*.ipynb                     # Env + config load, paths
    │           ├── 02_grids_*.ipynb                     # Ref grid + masks
    │           ├── 03_hazard_exposure_*.ipynb           # WBGT events; exposure
    │           ├── 04_impact_functions_*.ipynb          # Age differentiated IF; per-pixel deaths
    │           ├── 05_AC_*.ipynb                        # AC policy; downscaling AC penetration 
    │           ├── 06_vegetation_*.ipynb                # Vegetation/Tree policy
    │           ├── 07_downscale_electricity_*.ipynb     # Downscaling AC electricity 
    │           ├── 08_CBA_*.ipynb                       # Trees EAC/PV; AC CAPEX/maint/electricity PV; O&M vs CAPEX; benefits/costs; €/avoided; Budget-constrained mixes
    │
    ├── outputs/
    │   └── Rome/
    │   └── Genova/                                  
    │
    ├── pyproject.toml                               
    └── README.md                                    # this file 
```

---

## Environment Setup & Installation

### Requirements

- **Software:**
  - ... 
  - ... 

- **Packages:**  
  The analysis requires the following packages (and their dependencies):
  

### Installation Instructions

1. **Clone the Repository:**
   - Run the following command in your terminal:
     ```bash
     git clone 
     ```
   - Alternatively, download and extract the ZIP archive.

... 
   
### Data Access

- **Included Data:**  


### Datasets Overview

| id | Short name / file(s)                       | What it contains (granularity)                                | Years used |  Source    |Public link ↗︎ | Licence   |where to find?|
|----|--------------------------------------------|---------------------------------------------------------------|------------|------------|----------------|---------|--------------|

### Contact


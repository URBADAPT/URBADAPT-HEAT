# The socio-economic implications of public-private infrastructure interfaces in city-scale adaptation

Building a **generalisable pipeline** to study climate hazards and compare infrastructure adaptation strategies across cities. 

- Rome as a case-study, first example, not the end goal

- For a pool of European cities, how do different hazards (starting here with heat) translate into risk? 

- Which infrastructure adaptations (AC uptake, cool roofs, shade/trees, cooling centers…) for which criteria ? Ultimately: multi-criteria optimisation (minimisation of costs, maximisation of benefits, targeting the most vulnerable in priority…)

- How to make this repeatable and comparable across cities with minimum manual work? 

**Framework**: using as much as possible data that exist for different cities to build a reproducible pipeline. Ultimately designed to swap in new hazards, cities, and adaptation options with minimal refactoring. 

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
    │
    ├── configs/                                     # One YAML per city 
    │   ├── rome.yml
    │   └── barcelona.yml
    │
    ├── data/
    │   └── Rome/
    ├── notebooks/
    │   ├── 00_run_city.ipynb                        # Thin driver notebook (calls run_city)
    │   └── Rome/                                    # Split Rome workflow (current v0 logic)
    │       ├── 01_setup_*.ipynb                     # Env + config load, paths
    │       ├── 02_grids_*.ipynb                     # Ref grid + masks
    │       ├── 03_hazard_exposure_*.ipynb           # WBGT events; exposure
    │       ├── 04_impact_functions_*.ipynb          # Age differentiated IF; per-pixel deaths
    │       ├── 05_vegetation*.ipynb                 # Vegetation/Tree policy
    │       ├── 06_AC*.ipynb                         # AC policy; downscaling AC penetration
    │       ├── 07_costs_vegetation*.ipynb           # Trees EAC/PV; O&M vs CAPEX 
    │       ├── 08_costs_AC*.ipynb                   # AC CAPEX/maint/electricity PV 
    │       └── 09_downscale_electricity*.ipynb      # Downscaling AC electricity 
    │       ├── 10_CBA*.ipynb                        # benefits/costs; €/avoided 
    │       └── 11_budget_mix*.ipynb                 # Budget-constrained mixes
    │
    ├── outputs/
    │   └── Rome/                                    
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

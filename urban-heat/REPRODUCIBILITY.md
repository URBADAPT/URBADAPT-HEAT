# Reproducing the v1.0.1 pilot-city results

The results of the four pilot cities (Rome, Athens, Lisbon, Copenhagen), archived at
doi:10.5281/zenodo.24163360 (outputs) with their inputs at doi:10.5281/zenodo.24163362, were produced on the CMCC JUNO cluster with the scripts in `juno_jobs/` and the environment in
`environment.lock.yml`:

| Stage | Code | Settings |
|---|---|---|
| NB01-NB08 (deterministic chain) | commit `fa43dd2` (2026-10-19) | `IF_MAIN_FAMILY=masselot_tail_varcorr`, `T2M_ERA5_BIAS_CORRECT=1`, city configs in `configs/` |
| NB09 uncertainty, Rome and Lisbon | commit `fa43dd2` | N = 128 with the Burke bands, and N = 64; LHS seed 42 |
| NB09 uncertainty, Athens and Copenhagen | commit `13a3fe7` (2026-10-19) | as above |

`13a3fe7` differs from `fa43dd2` only by a guard in NB05 for years without cooling days, which does
not apply to these cities. This release is `13a3fe7` plus plotting-only changes (figure scripts, the
NB08 budget-figure axis and colours), pinned environments, the cluster job scripts and the table
generator; none of these changes alters a saved output.

## Verification (2026-11-12)

The deterministic chain NB01-NB08 was rerun for all four cities from the raw inputs at `13a3fe7`, in a
clean checkout and a fresh output variant (`juno_jobs/gmd_tagcheck.lsf`), and compared file by file
with the archived outputs using `validation/compare_output_trees.py`:

- 300 CSV/JSON output files are byte-identical, including every results table;
- 52 further files differ only in run metadata (output paths, the output-variant name, timestamps);
- the timestamped AC mortality series are byte-identical (their run-metadata files differ only in
  `created_at` and `run_id`);
- the only files not produced are the NB10 summary tables, since NB10 was not part of the rerun.

Run times on one core were 16-32 min per city, with peak memory 2.0-4.9 GB.

## Tables and figures

- Figures: `../visual_items/pilot_cities/` (`workflow_figures/make_figures.py`, `uncertainty/`), the
  template notebooks NB04-NB08 and NB10, and `cityheat/nb10_summary*.py`; the mapping of each
  published figure to its source is `../visual_items/pilot_cities/figure_manifest.csv`.
- Tables: `../visual_items/pilot_cities/workflow_tables/` (`make_pilot_tables.py` for the
  calibration, city-results and vulnerability-parameter tables; `make_uncertainty_matrix.py`;
  `../visual_items/pilot_cities/uncertainty/build_n_convergence.py`).

## Fitted inputs

- LST-T2M emulator: `fits/lst_t2m_emulator/` (scripts as run, session history, bundle and validation tables).
- Street-tree cooling coefficients: `fits/street_tree_cooling/` (URGED code snapshot and provenance).

## Inputs

See `DATA_SOURCES.md` for every input dataset, its source, version and licence, and whether it is
included in the archived input bundle.

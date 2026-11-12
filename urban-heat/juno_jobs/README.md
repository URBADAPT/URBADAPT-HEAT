# Cluster job scripts (CMCC JUNO, IBM Spectrum LSF)

The published v1.0.1 results were produced on the CMCC JUNO cluster with these scripts. Paths and
project codes are those of the original runs; adapt them to another cluster.

| Script | Role |
|---|---|
| `v3_group.lsf` | Runs a set of notebooks (default NB04-NB08) for a list of cities through `scripts/run_agnostic_batch.py`, with the canonical settings exported (`IF_MAIN_FAMILY=masselot_tail_varcorr`, `T2M_ERA5_BIAS_CORRECT=1`) and one output variant per run. |
| `submit_v3c.sh` | Submits the production run: NB05-NB08 per city, then NB09 chained on it. For the four pilot cities it submits NB09 twice through `../scripts/juno_run_nb09_gmd.lsf`: N = 128 with the Burke impact-function bands, and N = 64 for the convergence check (seed 42 in both). |
| `../scripts/juno_run_nb09_gmd.lsf` | NB09 uncertainty campaign for one city; the campaign id records the code commit. |
| `gmd_tagcheck.lsf` | Reproducibility check: reruns NB01-NB08 for one pilot city from the raw inputs with this release's code, into a fresh output variant, for comparison with the archived outputs. |

The environment is `../environment.lock.yml` (exact, linux-64) or `../environment.yml` (pinned direct
dependencies).

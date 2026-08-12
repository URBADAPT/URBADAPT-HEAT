#!/usr/bin/env bash
# =============================================================================
# Juno launcher — run ONLY NB09 (Monte-Carlo uncertainty) for the 40 cities,
# on top of the interim outputs produced by an earlier NB01..08 run.
#
# WHAT CHANGES vs the deterministic pipeline: the corrected NB09 engine now uses
# the neighbour EWS-interpretation bracket, AC CAPEX multipliers, t=1..T AC/tree
# discounting, annual aai_agg, and daily-percentile impact quantiles. These move
# outputs for ALL 40 cities (marginal cities gain 'intermediate', counterfactual
# cities gain 'intermediate', and the AC/discount/aai_agg fixes touch every city),
# so this is a full 40-city rerun — NOT a 12-city subset.
#
# First (clean) submission:  FRESH=1 bsub < scripts/juno_run_nb09.sh
# Resume after a timeout:            bsub < scripts/juno_run_nb09.sh   (no FRESH)
# =============================================================================

#### -------------------- LSF resource request --------------------------------
#BSUB -J urbadapt_nb09
#BSUB -P 0628
#BSUB -q s_long
#BSUB -n 1
#BSUB -M 32G
#BSUB -R "rusage[mem=32G]"
#BSUB -W 1440
#BSUB -o juno_logs/urbadapt_nb09.%J.out
#BSUB -e juno_logs/urbadapt_nb09.%J.err

set -euo pipefail
WORKERS=1

#### 0) repo
REPO="/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat"
cd "$REPO"
mkdir -p juno_logs
echo "==== $(date) | host $(hostname) | job ${LSB_JOBID:-local} | NB09, workers ${WORKERS} ===="

#### 1) guard: the CORRECTED engine must be present (neighbour bracket + AC multiplier
#    + daily-percentile quantiles). Sync manually first: git reset --hard origin/main
grep -q '_interp_brackets' cityheat/nb09_improved_fast.py \
  && grep -q 'AC_CAPEX_MULT_IDX' cityheat/nb09_improved_fast.py \
  && grep -q 'DAILY_QUANTILE_PCTS' cityheat/nb09_improved_fast.py \
  || { echo "ERROR: corrected NB09 not present — run 'git reset --hard origin/main' first."; exit 1; }
echo "OK: corrected NB09 engine present."

#### 2) NB09 run-tracking dir, SEPARATE from the 01..08 DONE markers.
#    FRESH=1 archives a prior NB09 run so --skip-completed restarts all 40 clean
#    (use it whenever the engine changed; otherwise resubmit to resume).
RUNS_DIR="$REPO/runs/agnostic_nb09"
export URBAN_HEAT_RUNS_DIR="$RUNS_DIR"
if [[ "${FRESH:-0}" == "1" && -d "$RUNS_DIR" ]]; then
  mv "$RUNS_DIR" "${RUNS_DIR}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "FRESH=1 -> archived previous NB09 run."
fi
mkdir -p "$RUNS_DIR"

#### 3) micromamba env (same as juno_run_all_sequential.sh — NOT conda)
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat

#### 4) jupyter kernel
python -m ipykernel install --user --name urbanheat --display-name urbanheat
export URBAN_HEAT_KERNEL="urbanheat"

#### 5) sanity check + the run (all 40 cities, one at a time, resumable)
python scripts/run_agnostic_batch.py --notebooks 09 --skip-completed --workers "${WORKERS}" --dry-run
python scripts/run_agnostic_batch.py --notebooks 09 --skip-completed --workers "${WORKERS}"

#### 6) how much is left
TOTAL=$(find calibration/preview_configs -name '*.yml' | wc -l)
DONE_N=$(find "$RUNS_DIR" -name DONE | wc -l)
echo "==== $(date) | ${DONE_N}/${TOTAL} cities NB09-DONE, $((TOTAL - DONE_N)) remaining ===="
(( DONE_N < TOTAL )) && echo "     resubmit to continue: bsub < scripts/juno_run_nb09.sh"
echo "     summary: ${RUNS_DIR}/summary.md"

# WHERE TO LOOK AFTERWARD
#   runs/agnostic_nb09/summary.md            -> per-city NB09 OK/FAIL
#   runs/agnostic_nb09/<city>/09.log         -> full console log of NB09
#   outputs_variants/masselot_main_agnostic/<city>/  -> the NB09 samples/CSVs/figures

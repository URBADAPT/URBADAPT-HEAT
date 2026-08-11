# Juno launcher — run ONLY NB09 (Monte-Carlo uncertainty) for the 40 cities,
# on top of the interim outputs produced by an earlier NB01..08 run.
#
# Why NB09-only is enough here:
#   The intermediate-EWS change lives entirely in the NB09 engine
#   (cityheat/nb09_improved_fast.py, inherited by nb09_improved_fast_masselot_main.py).
#   NB06/NB08 already emit the intermediate case and their outputs are UNCHANGED, so
#   NB09 just needs the existing hazard/impact/AC/EWS/tree interim artifacts on disk
#   (from your clean 01..08 run) — it does NOT require re-running NB01..08.
#   NB09 reads efficacy_marginal/counterfactual + interpretation straight from the
#   config, so no config change is needed either.
#
# Submit with:   bsub < scripts/juno_run_nb09.sh
# Resume:        just submit again — --skip-completed skips cities already DONE.
#
# >>> FILL THE <PLACEHOLDERS> (same values as juno_run_agnostic.sh). <<<

#### LSF resource request (edit these)
#BSUB -J urbadapt40_nb09                 # job name
#BSUB -P <PROJECT_ID>                    # CMCC/Juno project/account
#BSUB -q <COMPUTE_QUEUE>                 # a queue that ALLOWS the walltime below
#BSUB -n 4                               # cores
#BSUB -M 32000                           # per-process memory limit (MB)
#BSUB -R "rusage[mem=32000]"             # reserve 32 GB
#BSUB -W 24:00                           # walltime; NB09 at N=512 is heavy — job is resumable if it runs out
#BSUB -o juno_logs/urbadapt40_nb09.%J.out
#BSUB -e juno_logs/urbadapt40_nb09.%J.err

set -euo pipefail

#### 0) locate the repo (edit REPO)
REPO="<PATH_ON_JUNO>/URBADAPT-HEAT/urban-heat"     # <-- absolute path to the repo's urban-heat/ on Juno
cd "$REPO"
mkdir -p juno_logs

echo "==== $(date) | host $(hostname) | job ${LSB_JOBID:-local} ===="

#### 1) CHECK the patched NB09 engine is present (avoid the stale-checkout trap)
#    Do the actual sync MANUALLY before submitting (so you don't clobber any
#    uncommitted Juno-local edits), e.g. from the repo root on Juno:
#        git fetch origin && git reset --hard origin/main
#    This step only *reports* HEAD and fails fast if the fix isn't in the file.
git -C "$REPO/.." fetch origin --quiet || true
echo "HEAD:        $(git -C "$REPO/.." rev-parse --short HEAD)"
echo "origin/main: $(git -C "$REPO/.." rev-parse --short origin/main)"
grep -q 'elif _interp == "intermediate"' cityheat/nb09_improved_fast.py \
  || { echo "ERROR: intermediate-EWS patch NOT in cityheat/nb09_improved_fast.py — pull origin/main first."; exit 1; }
echo "OK: intermediate-EWS patch present in the NB09 engine."

#### 2) conda env + jupyter kernel (same as juno_run_agnostic.sh)
module load conda 2>/dev/null || module load miniforge 2>/dev/null || true
source activate urbanheat 2>/dev/null || conda activate urbanheat
python -m ipykernel install --user --name urbanheat --display-name urbanheat
export URBAN_HEAT_KERNEL="urbanheat"

#### 3) keep NB09 run-tracking SEPARATE from the 01..08 DONE markers
#    (so --skip-completed tracks NB09 completion cleanly, no collision, no rm needed)
export URBAN_HEAT_RUNS_DIR="$REPO/runs/agnostic_nb09"

# Optional knobs (defaults come from the notebook: N=512, seed=42, figures on):
# export NB09_N=512
# export NB09_SEED=42

#### 4) sanity check (fails fast if env/kernel/notebook/config missing)
python scripts/run_agnostic_batch.py --notebooks 09 --skip-completed --workers 1 --dry-run

#### 5) THE RUN: NB09 for all 40 cities, one at a time, resumable
python scripts/run_agnostic_batch.py --notebooks 09 --skip-completed --workers 1

echo "==== $(date) | finished. Summary: $URBAN_HEAT_RUNS_DIR/summary.md ===="

# WHERE TO LOOK AFTERWARD
#   runs/agnostic_nb09/summary.md          -> per-city: NB09 OK/FAIL + error
#   runs/agnostic_nb09/<city>/09.log       -> full console log of NB09
#   runs/agnostic_nb09/<city>/09.ipynb     -> executed NB09 (full traceback in-cell if it failed)
#   runs/agnostic_nb09/<city>/DONE         -> present iff that city's NB09 finished
#   outputs_variants/masselot_main_agnostic/<city>/  -> the NB09 samples/CSVs/figures themselves
#
# ONLY THE CHANGED CITIES (cheaper): the intermediate-EWS numbers move for the 12
# meteo-HHWS (tier-2) cities only; the other 28 are provably unchanged. To refresh
# just those, add:
#   --cities amsterdam berlin brussels budapest cologne hamburg munich rotterdam vienna bucharest helsinki stockholm

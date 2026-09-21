# Juno launcher — run March2026_agnostic notebooks 01..08 for the 40
# cities, SEQUENTIALLY, continuing to the next city if one notebook fails.
#
# Submit with:   bsub < scripts/juno_run_agnostic.sh
# Resume:        just submit again — --skip-completed skips cities already DONE.
#
# The heavy lifting (sequential loop, per-notebook logs, error capture, skip-on-
# failure, resumability) is in scripts/run_agnostic_batch.py. This file only wraps
# it for LSF + sets up the conda env + jupyter kernel.
#
# >>> FILL THE <PLACEHOLDERS> for your Juno account/queue before submitting. <<<

#### LSF resource request (edit these) 
#BSUB -J urbadapt40                     # job name
#BSUB -P <PROJECT_ID>                   # CMCC/Juno project/account
#BSUB -q <COMPUTE_QUEUE>                # e.g. p_short / p_medium / s_long (a queue that ALLOWS the walltime below)
#BSUB -n 4                              # cores (CLIMADA hazard build is mostly single-threaded; 4 is comfortable)
#BSUB -M 32000                          # per-process memory limit (MB) 
#BSUB -R "rusage[mem=32000]"            # reserve 32 GB
#BSUB -W 24:00                          # walltime HH:MM => biggest that queue allows; job is resumable if it runs out
#BSUB -o juno_logs/urbadapt40.%J.out    # LSF stdout  (%J = job id)
#BSUB -e juno_logs/urbadapt40.%J.err    # LSF stderr
# NOTE: if compute nodes have NO internet, this direct run will fail at NB01/NB02
#       downloads — see the "OFFLINE / s_download" section at the bottom.

set -euo pipefail

#### 0) locate the repo (edit REPO) 
REPO="<PATH_ON_JUNO>/URBADAPT-HEAT/urban-heat"     # <-- absolute path to the repo's urban-heat/ on Juno
cd "$REPO"
mkdir -p juno_logs

echo "==== $(date) | host $(hostname) | job ${LSB_JOBID:-local} ===="

#### 1) conda env 
module load conda 2>/dev/null || module load miniforge 2>/dev/null || true   # <-- need to be adjusted to Juno's module name
# Create the env once (comment out after first successful creation), then activate:
#   conda env create -f environment.yml            # if you have one, OR:
#   conda create -y -n urbanheat python=3.10 && conda run -n urbanheat pip install -r requirements.txt
source activate urbanheat 2>/dev/null || conda activate urbanheat

#### 2) register a jupyter kernel so nbconvert can find it 
# The template notebooks request a kernel named "urbanheat"; register one that
# points at THIS env, then tell the runner to use it.
python -m ipykernel install --user --name urbanheat --display-name urbanheat
export URBAN_HEAT_KERNEL="urbanheat"

# Optional: keep run logs on scratch instead of inside the repo:
# export URBAN_HEAT_RUNS_DIR="<SCRATCH>/urbadapt_runs"

#### 3) sanity check (fails fast if env/kernel/notebooks/config missing)
python scripts/run_agnostic_batch.py --skip-completed --workers 1 --dry-run

#### 4) THE RUN: 40 cities, one at a time, 01..08, skip-on-failure 
# --workers 1  -> strictly sequential (finish one city before the next)
# --skip-completed -> resumable; re-submitting continues where it left off
python scripts/run_agnostic_batch.py --skip-completed --workers 1

echo "==== $(date) | finished. Summary: runs/agnostic_batch/summary.md ===="

# WHERE TO LOOK AFTERWARD
#   runs/agnostic_batch/summary.md      -> per-city: last NB passed, failing NB, error
#   runs/agnostic_batch/summary.json    -> same, machine-readable
#   runs/agnostic_batch/<city>/<NN>.log -> full console log of each notebook
#   runs/agnostic_batch/<city>/<NN>.ipynb -> executed notebook (full traceback in-cell)
#   runs/agnostic_batch/<city>/DONE     -> present iff that city finished 01..08
#
# OFFLINE / s_download (only if the direct run above hits a download firewall)
#   The pipeline downloads during the run (Drive=NB01, UrbClim+WorldPop=NB02,
#   OSM/Overpass=NB05). If compute nodes are offline, pre-stage first:
#   (a) rsync the already-downloaded data/ dir from the local machine to $REPO/data/, OR
#   (b) submit a job to the internet-facing s_download queue that runs the SAME
#       runner (it will download + cache everything; the sync skips files that are
#       already present via .synced sidecars), THEN run this compute job offline.
#   The NB02 UrbClim/WorldPop and NB05 Overpass caches also live under data/<city>/,
#   so a full data/ pre-stage makes the compute phase internet-free. Would need to 
# write s_download pre-stage script once we know if it's needed.

#!/usr/bin/env bash
# Submit NB09 as a serial LSF job array: all selected city jobs are queued at
# once, but the "%1" concurrency limit permits only one city to run at a time.
# This script must be run on a Juno login node from the synced urban-heat repo.

set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  central|smoke|production) ;;
  *)
    echo "Usage: $0 {central|smoke|production}" >&2
    exit 2
    ;;
esac

REPO="${URBAN_HEAT_REPO:-/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat}"
cd "$REPO"
LAUNCHER="$REPO/scripts/juno_run_nb09.sh"
[[ -x "$LAUNCHER" ]] || { echo "ERROR: launcher is missing or not executable: $LAUNCHER" >&2; exit 1; }

git diff --quiet -- . \
  || { echo "ERROR: unstaged tracked changes under urban-heat; commit and sync first." >&2; exit 1; }
git diff --cached --quiet -- . \
  || { echo "ERROR: staged but uncommitted changes under urban-heat; commit and sync first." >&2; exit 1; }

# The Juno login-node default Python is older than the project requires.  Load
# the same environment used by the workers before importing even the
# standard-library-only batch roster module.
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat

mapfile -t ALL_CITIES < <(python scripts/run_agnostic_batch.py --list-cities)
[[ "${#ALL_CITIES[@]}" -eq 40 ]] \
  || { echo "ERROR: expected the frozen 40-city roster, found ${#ALL_CITIES[@]}." >&2; exit 1; }

export NB09_SEED="${NB09_SEED:-42}"
export NB09_OUTPUT_SCHEMA_TAG="${NB09_OUTPUT_SCHEMA_TAG:-v3}"
case "$MODE" in
  central)
    export NB09_N="${NB09_N:-128}"
    export CENTRAL_ONLY=1
    CITIES=("${ALL_CITIES[@]}")
    ;;
  smoke)
    export NB09_N="${NB09_N:-8}"
    export CENTRAL_ONLY=0
    # Bologna exercises the updated greening input; the remainder span hot and
    # cool climates and the three EWS maturity interpretations.
    smoke_text="${NB09_SMOKE_CITIES:-bologna,athens,copenhagen,rome,berlin,warsaw}"
    smoke_text="${smoke_text//,/ }"
    read -r -a CITIES <<< "$smoke_text"
    ;;
  production)
    export NB09_N="${NB09_N:-128}"
    [[ "$NB09_N" == "128" ]] \
      || { echo "ERROR: production is locked to NB09_N=128; use smoke for small-N testing." >&2; exit 1; }
    export CENTRAL_ONLY=0
    CITIES=("${ALL_CITIES[@]}")
    ;;
esac

[[ "$NB09_SEED" =~ ^-?[0-9]+$ ]] || { echo "ERROR: NB09_SEED must be an integer." >&2; exit 1; }
[[ "$NB09_N" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: NB09_N must be positive." >&2; exit 1; }
for city in "${CITIES[@]}"; do
  [[ " ${ALL_CITIES[*]} " == *" ${city} "* ]] \
    || { echo "ERROR: ${city} is not in the frozen 40-city roster." >&2; exit 1; }
done

HEAD_FULL="$(git rev-parse HEAD)"
HEAD_SHORT="$(git rev-parse --short=12 HEAD)"
if [[ "$MODE" != "central" ]]; then
  PREFLIGHT_MARKER="$REPO/runs/agnostic_nb09/central_preflight/${NB09_OUTPUT_SCHEMA_TAG}_${HEAD_FULL}.ok"
  [[ -f "$PREFLIGHT_MARKER" ]] \
    || { echo "ERROR: the all-city central-parity gate is missing for ${HEAD_SHORT}." >&2;
         echo "Run '$0 central' and inspect its results first." >&2; exit 1; }
fi

export NB09_ARRAY_CITIES="$(IFS=,; echo "${CITIES[*]}")"
unset NB09_CITIES
unset LSB_JOBINDEX

STAMP="$(date +%Y%m%d_%H%M%S)"
SUBMISSION_DIR="$REPO/runs/agnostic_nb09/submissions"
mkdir -p "$SUBMISSION_DIR" "$REPO/juno_logs"
MANIFEST="$SUBMISSION_DIR/${STAMP}_${MODE}_n${NB09_N}_seed${NB09_SEED}_${HEAD_SHORT}.tsv"
JOB_BASE="nb09_${MODE:0:4}_${HEAD_SHORT:0:7}"
ARRAY_SPEC="${JOB_BASE}[1-${#CITIES[@]}]%1"

echo "Submitting ${MODE}: ${#CITIES[@]} city jobs, serial concurrency %1."
echo "N=${NB09_N}; seed=${NB09_SEED}; commit=${HEAD_FULL}"
SUBMIT_TEXT="$(bsub -J "$ARRAY_SPEC" < "$LAUNCHER")"
echo "$SUBMIT_TEXT"
JOB_ID_RE='Job[[:space:]]*<([0-9]+)>'
if [[ "$SUBMIT_TEXT" =~ $JOB_ID_RE ]]; then
  ARRAY_JOB_ID="${BASH_REMATCH[1]}"
else
  echo "ERROR: could not parse the LSF array job ID." >&2
  exit 1
fi

{
  printf 'mode\tarray_job_id\tarray_index\tcity\tn_samples\tseed\tgit_commit\n'
  for i in "${!CITIES[@]}"; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$MODE" "$ARRAY_JOB_ID" "$((i + 1))" "${CITIES[$i]}" \
      "$NB09_N" "$NB09_SEED" "$HEAD_FULL"
  done
} > "$MANIFEST"

if [[ "$MODE" == "central" ]]; then
  # This ordinary (non-array) job runs after every central array element has
  # ended. Successful city controls are reused and verified; only if all 40
  # pass does the launcher create the commit-specific all-city gate marker.
  unset NB09_ARRAY_CITIES
  GATE_TEXT="$(bsub -J "${JOB_BASE}_gate" -w "ended(${ARRAY_JOB_ID})" < "$LAUNCHER")"
  echo "$GATE_TEXT"
  if [[ "$GATE_TEXT" =~ $JOB_ID_RE ]]; then
    GATE_JOB_ID="${BASH_REMATCH[1]}"
  else
    echo "ERROR: array was submitted, but the final gate job ID could not be parsed." >&2
    exit 1
  fi
  printf '# final_gate_job_id\t%s\n' "$GATE_JOB_ID" >> "$MANIFEST"
  echo "Central array ${ARRAY_JOB_ID} and final gate ${GATE_JOB_ID} are queued."
else
  echo "Serial ${MODE} array ${ARRAY_JOB_ID} is queued."
fi

echo "Submission manifest: $MANIFEST"
echo "Monitor without affecting the jobs: bjobs -A ${ARRAY_JOB_ID}"
echo "You may disconnect from Juno and close the laptop after this command returns."

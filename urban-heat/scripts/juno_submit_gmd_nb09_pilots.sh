#!/usr/bin/env bash
# Queue the complete GMD NB09 completion campaign once, serially (%1), plus a
# final validation gate. The jobs continue after logout/laptop shutdown.

set -euo pipefail

REPO="${URBAN_HEAT_REPO:-/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat}"
cd "$REPO"
LAUNCHER="$REPO/scripts/juno_run_gmd_nb09_pilots.sh"
[[ -x "$LAUNCHER" ]] || { echo "ERROR: missing executable launcher: $LAUNCHER"; exit 1; }

git diff --quiet -- . \
  || { echo "ERROR: unstaged tracked changes under urban-heat; commit and sync first"; exit 1; }
git diff --cached --quiet -- . \
  || { echo "ERROR: staged but uncommitted changes under urban-heat; commit and sync first"; exit 1; }

SEED=42
HEAD_FULL="$(git rev-parse HEAD)"
HEAD_SHORT="$(git rev-parse --short=12 HEAD)"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p juno_logs runs/agnostic_nb09/gmd_pilots/submissions
MANIFEST="runs/agnostic_nb09/gmd_pilots/submissions/${STAMP}_gmd4_seed${SEED}_${HEAD_SHORT}.tsv"
JOB_BASE="gmd_nb09_${HEAD_SHORT:0:7}"

# Juno's login-node default Python is older than the project runtime. Use the
# same environment as the worker jobs even for the lightweight preflight.
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat

# Reuse is allowed only after confirming that the four authoritative corrected
# N=128 Masselot-headline results are present on Juno.
python - "$REPO" <<'PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
campaign = "n128_seed42_v3_616fbc2d5131"
for city in ("athens", "copenhagen", "lisbon", "rome"):
    marker = (
        repo / "outputs_variants" / "masselot_main_agnostic" / city / "tables"
        / "uncertainty_runs" / campaign / "CAMPAIGN_COMPLETE.json"
    )
    if not marker.is_file():
        raise SystemExit(f"ERROR: missing authoritative N=128 Masselot marker: {marker}")
    payload = json.loads(marker.read_text())
    if payload.get("campaign_id") != campaign or payload.get("n_samples") != 128:
        raise SystemExit(f"ERROR: invalid authoritative N=128 Masselot marker: {marker}")
    print(f"PASS: reusing corrected N=128 Masselot headline for {city}")
PY

echo "Submitting a three-scope N=8 smoke array, then 12 GMD pilot tasks at serial concurrency %1:"
echo "  4 x N=64 Masselot headline"
echo "  4 x N=128 Burke polynomial conditional"
echo "  4 x N=128 Burke power-law conditional"
echo "The validated N=128 Masselot headline is reused and is not rerun."

SMOKE_TEXT="$(bsub -J "${JOB_BASE}_smoke[1-3]%1" \
  -env "all,GMD_NB09_SMOKE_ONLY=1" < "$LAUNCHER")"
echo "$SMOKE_TEXT"
JOB_ID_RE='Job[[:space:]]*<([0-9]+)>'
[[ "$SMOKE_TEXT" =~ $JOB_ID_RE ]] \
  || { echo "ERROR: could not parse smoke-array job ID"; exit 1; }
SMOKE_JOB_ID="${BASH_REMATCH[1]}"

SUBMIT_TEXT="$(bsub -J "${JOB_BASE}[1-12]%1" -w "done(${SMOKE_JOB_ID})" < "$LAUNCHER")"
echo "$SUBMIT_TEXT"
[[ "$SUBMIT_TEXT" =~ $JOB_ID_RE ]] \
  || { echo "ERROR: could not parse array job ID"; exit 1; }
ARRAY_JOB_ID="${BASH_REMATCH[1]}"

GATE_TEXT="$(bsub -J "${JOB_BASE}_gate" -w "ended(${ARRAY_JOB_ID})" \
  -env "all,GMD_NB09_GATE_ONLY=1,GMD_NB09_SEED=${SEED}" < "$LAUNCHER")"
echo "$GATE_TEXT"
[[ "$GATE_TEXT" =~ $JOB_ID_RE ]] \
  || { echo "ERROR: array submitted but gate job ID could not be parsed"; exit 1; }
GATE_JOB_ID="${BASH_REMATCH[1]}"

{
  printf '# smoke_array_job_id\t%s\n' "$SMOKE_JOB_ID"
  printf 'array_job_id\tarray_index\tcity\tscope\tn_samples\tseed\tlauncher_commit\n'
  cities=(athens copenhagen lisbon rome)
  scopes=(masselot_headline burke_polynomial burke_powerlaw)
  index=0
  for scope in "${scopes[@]}"; do
    if [[ "$scope" == "masselot_headline" ]]; then n=64; else n=128; fi
    for city in "${cities[@]}"; do
      index=$((index + 1))
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$ARRAY_JOB_ID" "$index" "$city" "$scope" "$n" "$SEED" "$HEAD_FULL"
    done
  done
  printf '# final_gate_job_id\t%s\n' "$GATE_JOB_ID"
} > "$MANIFEST"

echo
echo "Smoke array ${SMOKE_JOB_ID}, production array ${ARRAY_JOB_ID}, and final gate ${GATE_JOB_ID} are queued."
echo "Submission manifest: $REPO/$MANIFEST"
echo "Smoke:  bjobs -A ${SMOKE_JOB_ID}"
echo "Main:   bjobs -A ${ARRAY_JOB_ID}"
echo "Gate:    bjobs -w ${GATE_JOB_ID}"
echo "You may disconnect from Juno; production releases automatically only after the smoke array passes."

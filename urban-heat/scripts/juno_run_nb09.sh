#!/usr/bin/env bash
# =============================================================================
# Juno launcher — final Masselot-headline NB09 uncertainty workflow.
#
# Required production sequence:
#   CENTRAL_ONLY=1 bsub < scripts/juno_run_nb09.sh  # all-city parity gate
#   bsub < scripts/juno_run_nb09.sh                 # final N=128 campaign
#
# A timeout is resumed with the same command. Completed sample checkpoints and
# city DONE markers are reused only when their code/input/design provenance
# still matches. FRESH=1 is deliberately unsupported: use a new campaign ID
# for a genuinely different experiment.
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
WORKERS="${NB09_WORKERS:-1}"
export NB09_N="${NB09_N:-128}"
export NB09_SEED="${NB09_SEED:-42}"
CENTRAL_ONLY="${CENTRAL_ONLY:-0}"
OUTPUT_SCHEMA_TAG="${NB09_OUTPUT_SCHEMA_TAG:-v3}"
[[ "$NB09_N" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: NB09_N must be a positive integer."; exit 1; }
[[ "$NB09_SEED" =~ ^-?[0-9]+$ ]] || { echo "ERROR: NB09_SEED must be an integer."; exit 1; }
[[ "$CENTRAL_ONLY" == "0" || "$CENTRAL_ONLY" == "1" ]] \
  || { echo "ERROR: CENTRAL_ONLY must be 0 or 1."; exit 1; }
[[ "$OUTPUT_SCHEMA_TAG" == "v3" ]] \
  || { echo "ERROR: this launcher requires NB09_OUTPUT_SCHEMA_TAG=v3."; exit 1; }

#### 0) repository and immutable campaign identity
REPO="${URBAN_HEAT_REPO:-/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat}"
cd "$REPO"
mkdir -p juno_logs
CURRENT_HEAD="$(git rev-parse HEAD)"
SHORT_HEAD="$(git rev-parse --short=12 HEAD)"
export NB09_CAMPAIGN_ID="${NB09_CAMPAIGN_ID:-n${NB09_N}_seed${NB09_SEED}_${OUTPUT_SCHEMA_TAG}_${SHORT_HEAD}}"
[[ "$NB09_CAMPAIGN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] \
  || { echo "ERROR: invalid NB09_CAMPAIGN_ID=${NB09_CAMPAIGN_ID}"; exit 1; }
echo "==== $(date) | host $(hostname) | job ${LSB_JOBID:-local} | campaign ${NB09_CAMPAIGN_ID} ===="

#### 1) code and clean-source guards
grep -q '_interp_brackets' cityheat/nb09_improved_fast.py \
  && grep -q 'AC_CAPEX_MULT_IDX' cityheat/nb09_improved_fast.py \
  && grep -q 'DAILY_QUANTILE_PCTS' cityheat/nb09_improved_fast.py \
  && grep -q 'coverage_full_for_exposure' cityheat/nb09_improved_fast.py \
  && grep -q 'coverage_series_for_waste_heat' cityheat/nb09_improved_fast.py \
  && grep -q 'ac_cba_population_series' cityheat/nb09_improved_fast.py \
  && grep -q 'prepare_campaign' cityheat/nb09_improved_fast.py \
  && grep -q 'validate_sample_output' cityheat/nb09_improved_fast.py \
  && grep -q 'unc_policy_trajectories_25y' cityheat/nb09_improved_fast.py \
  && grep -q 'OUTPUT_SCHEMA_VERSION = "3.0-explicit-policy-trajectories"' cityheat/nb09_improved_fast.py \
  || { echo "ERROR: final parity-controlled NB09 engine is not present."; exit 1; }

# Generated results and untracked inputs are intentionally ignored here. Every
# tracked change below urban-heat must be committed before production because
# NB09 also imports shared cityheat modules; a narrower path list could let an
# edited dependency run under a Git commit that does not describe the code.
git diff --quiet -- . \
  || { echo "ERROR: unstaged tracked changes under urban-heat; commit and sync first."; exit 1; }
git diff --cached --quiet -- . \
  || { echo "ERROR: staged but uncommitted changes under urban-heat; commit and sync first."; exit 1; }
echo "OK: final engine is present and all tracked production sources are clean."

#### 2) campaign-isolated run tracking
RUNS_DIR="${URBAN_HEAT_RUNS_DIR:-$REPO/runs/agnostic_nb09/$NB09_CAMPAIGN_ID}"
export URBAN_HEAT_RUNS_DIR="$RUNS_DIR"
[[ "${FRESH:-0}" != "1" ]] \
  || { echo "ERROR: FRESH=1 is disabled. Use a new NB09_CAMPAIGN_ID for a new experiment."; exit 1; }
mkdir -p "$RUNS_DIR"

#### 3) runtime (same micromamba environment as NB01--08)
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat

if jupyter kernelspec list --json \
  | python -c 'import json,sys; sys.exit(0 if "urbanheat" in json.load(sys.stdin).get("kernelspecs", {}) else 1)'; then
  echo "Reusing existing Jupyter kernel: urbanheat"
else
  python -m ipykernel install --user --name urbanheat --display-name urbanheat
fi
export URBAN_HEAT_KERNEL="urbanheat"
export NB09_BURKE_SENSITIVITY=0

#### 4) frozen roster and optional smoke-test subset
mapfile -t ALL_CITIES < <(python scripts/run_agnostic_batch.py --list-cities)
[[ "${#ALL_CITIES[@]}" -eq 40 ]] \
  || { echo "ERROR: expected 40 production cities, found ${#ALL_CITIES[@]}."; exit 1; }
if [[ -n "${NB09_CITIES:-}" ]]; then
  city_text="${NB09_CITIES//,/ }"
  read -r -a CITIES <<< "$city_text"
  [[ "${#CITIES[@]}" -gt 0 ]] || { echo "ERROR: NB09_CITIES is empty."; exit 1; }
  for city in "${CITIES[@]}"; do
    [[ " ${ALL_CITIES[*]} " == *" ${city} "* ]] \
      || { echo "ERROR: ${city} is not in the frozen 40-city roster."; exit 1; }
  done
else
  CITIES=("${ALL_CITIES[@]}")
fi
CITY_SET_KEY="$(printf '%s\n' "${CITIES[@]}" | sha256sum | awk '{print substr($1,1,12)}')"
export URBAN_HEAT_SUMMARY_STEM="summary_${CITY_SET_KEY}"

#### 5) explicit all-city central-parity gate
PREFLIGHT_DIR="$REPO/runs/agnostic_nb09/central_preflight"
PREFLIGHT_MARKER="$PREFLIGHT_DIR/${OUTPUT_SCHEMA_TAG}_${CURRENT_HEAD}.ok"
mkdir -p "$PREFLIGHT_DIR"
if [[ "$CENTRAL_ONLY" == "1" ]]; then
  [[ "${#CITIES[@]}" -eq 40 ]] \
    || { echo "ERROR: CENTRAL_ONLY=1 must cover all 40 cities; unset NB09_CITIES."; exit 1; }
  echo "Running central NB01--NB08 parity preflight for all 40 cities..."
  for city in "${ALL_CITIES[@]}"; do
    echo "---- central parity: ${city} ----"
    URBAN_HEAT_OUTPUT_VARIANT=masselot_main_agnostic \
    IF_MAIN_FAMILY=masselot_tail \
    HAZARD_TRACK=standard \
    NB09_BURKE_SENSITIVITY=0 \
      python -m cityheat.nb09_improved_fast_masselot_main \
        --city "$city" --n "$NB09_N" --seed "$NB09_SEED" --central-check-only
  done
  preflight_tmp="${PREFLIGHT_MARKER}.tmp.${LSB_JOBID:-$$}"
  printf '%s\n%s\n%s\n%s\n' \
    "$CURRENT_HEAD" "$OUTPUT_SCHEMA_TAG" "$NB09_CAMPAIGN_ID" "$(date -Iseconds)" \
    > "$preflight_tmp"
  command mv -f "$preflight_tmp" "$PREFLIGHT_MARKER"
  echo "All 40 central parity checks passed. No LHS sample was evaluated."
  exit 0
fi

[[ -f "$PREFLIGHT_MARKER" ]] \
  || { echo "ERROR: all-city central preflight is missing for this commit/schema."; \
       echo "Submit first with: CENTRAL_ONLY=1 bsub < scripts/juno_run_nb09.sh"; exit 1; }
[[ "$(sed -n '1p' "$PREFLIGHT_MARKER")" == "$CURRENT_HEAD" \
   && "$(sed -n '2p' "$PREFLIGHT_MARKER")" == "$OUTPUT_SCHEMA_TAG" ]] \
  || { echo "ERROR: central-preflight marker content is invalid: $PREFLIGHT_MARKER"; exit 1; }
echo "Reusing all-city central-parity gate for commit ${CURRENT_HEAD}."

#### 6) dry run and resumable LHS execution
echo "NB09 LHS sample size: ${NB09_N}"
echo "NB09 LHS seed: ${NB09_SEED}"
echo "NB09 cities: ${CITIES[*]}"
NB09_TIMEOUT="${NB09_TIMEOUT:-72000}"
python scripts/run_agnostic_batch.py \
  --cities "${CITIES[@]}" --notebooks 09 --skip-completed \
  --workers "$WORKERS" --timeout "$NB09_TIMEOUT" --dry-run
python scripts/run_agnostic_batch.py \
  --cities "${CITIES[@]}" --notebooks 09 --skip-completed \
  --workers "$WORKERS" --timeout "$NB09_TIMEOUT"

#### 7) completion summary
TOTAL="${#CITIES[@]}"
DONE_N=0
for city in "${CITIES[@]}"; do
  [[ -f "$RUNS_DIR/$city/DONE" ]] && DONE_N=$((DONE_N + 1))
done
echo "==== $(date) | ${DONE_N}/${TOTAL} cities NB09-DONE, $((TOTAL - DONE_N)) remaining ===="
(( DONE_N < TOTAL )) && echo "     resubmit the identical command to continue"
echo "     summary: ${RUNS_DIR}/${URBAN_HEAT_SUMMARY_STEM}.md"

# Outputs:
#   runs/agnostic_nb09/<campaign>/summary.md
#   runs/agnostic_nb09/<campaign>/<city>/09.log
#   outputs_variants/masselot_main_agnostic/<city>/tables/
#     uncertainty_runs/<campaign>/

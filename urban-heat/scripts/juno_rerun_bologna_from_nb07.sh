#!/usr/bin/env bash
# =============================================================================
# Bologna-only refresh after adding the previously missing GVI--LST coefficients.
#
# Prerequisite: stage the validated coefficient table at
#   data/bologna/CoolingEff/coefs_GVI_lst.csv
# The guard below requires the exact append-only artifact prepared on 2026-09-03.
#
# Submit with:
#   bsub < scripts/juno_rerun_bologna_from_nb07.sh
# =============================================================================

#BSUB -J bologna_gvi_0710
#BSUB -P 0628
#BSUB -q s_long
#BSUB -n 1
#BSUB -M 32G
#BSUB -R "rusage[mem=32G]"
#BSUB -W 1440
#BSUB -o juno_logs/bologna_gvi_0710.%J.out
#BSUB -e juno_logs/bologna_gvi_0710.%J.err

set -euo pipefail

REPO="/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat"
COEFF="$REPO/data/bologna/CoolingEff/coefs_GVI_lst.csv"
EXPECTED_SHA256="04fc7bd9de2b45bfb3ed31fe8a56a39ba21c6401e6ea475e51442495ec5fae9f"
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "$REPO"
mkdir -p juno_logs

if [[ ! -f "$COEFF" ]]; then
  echo "ERROR: missing staged coefficient table: $COEFF" >&2
  exit 1
fi

ACTUAL_SHA256="$(sha256sum "$COEFF" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "ERROR: coefficient-table checksum mismatch." >&2
  echo "Expected: $EXPECTED_SHA256" >&2
  echo "Actual:   $ACTUAL_SHA256" >&2
  exit 1
fi

# Keep a recoverable snapshot of all previous Bologna products before NB07--10
# overwrite their canonical output paths.
RESULTS="$REPO/outputs_variants/masselot_main_agnostic/bologna"
if [[ -d "$RESULTS" ]]; then
  cp -a "$RESULTS" "${RESULTS}.pre_gvi_${STAMP}"
  echo "Archived prior Bologna outputs: ${RESULTS}.pre_gvi_${STAMP}"
fi

export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat

python -c 'import csv,sys; p=sys.argv[1]; rows=list(csv.DictReader(open(p, newline=""))); b=[r for r in rows if r["city"]=="Bologna"]; assert len(rows)==4017 and len(b)==36 and len({(r["city"],r["lcz"],r["month"]) for r in rows})==4017, "coefficient-table structural check failed"; print(f"OK: {len(rows)} rows, {len(b)} Bologna rows, all keys unique")' "$COEFF"

python -m ipykernel install --user --name urbanheat --display-name urbanheat
export URBAN_HEAT_KERNEL="urbanheat"
export NB09_N=128

# Use a dedicated run-tracking directory so the original 01--08 and all-city
# NB09 DONE markers remain untouched.
export URBAN_HEAT_RUNS_DIR="$REPO/runs/bologna_gvi_refresh_n128_${STAMP}"

python scripts/run_agnostic_batch.py \
  --cities bologna \
  --notebooks 07 08 09 10 \
  --workers 1 \
  --timeout 72000 \
  --dry-run

python scripts/run_agnostic_batch.py \
  --cities bologna \
  --notebooks 07 08 09 10 \
  --workers 1 \
  --timeout 72000

test -f "$URBAN_HEAT_RUNS_DIR/bologna/DONE"
echo "Bologna NB07--10 refresh complete."
echo "Run record: $URBAN_HEAT_RUNS_DIR"

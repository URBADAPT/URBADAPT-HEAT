#!/usr/bin/env bash
# Pull the corrected output trees for the four GMD pilot cities, so the manuscript
# figures can be regenerated against the calibrated run.
#
# Usage:
#   bash scripts/pull_gmd_pilots.sh [figures|full]
#
#   figures  (default) everything the figure pipeline reads: tables, the interim CSV/
#            JSON/NPZ/GPKG grids, the FUA and LCZ geometry. No hazard rasters, so the
#            T2M map figure cannot be rebuilt from this mode.
#   full     the above plus hazard/T2M_daily_mean_*_FUA_degC.nc, which is what the
#            hazard map figure needs and is also the only product that visibly carries
#            the ERA5 reference correction. Several hundred MB per city.
#
# WHY A SEPARATE SCRIPT FROM pull_missing_cities.sh
# That one deliberately fetches only the two tables the calibration comparison reads,
# for 40 cities. The manuscript figures need far more per city but only for four, so
# the selective dimension is the opposite one. Running the 40-city script in `full`
# mode would transfer the 128-draw uncertainty trees for every city - gigabytes that
# no figure reads.
#
# Resumable: rsync skips files already present with the same size and mtime.

set -uo pipefail

MODE="${1:-figures}"
case "$MODE" in figures|full) ;; *) echo "usage: bash scripts/pull_gmd_pilots.sh [figures|full]"; exit 2 ;; esac

CITIES="${CITIES:-rome athens lisbon copenhagen}"
SSH_HOST="${JUNO_HOST:-juno}"
REMOTE="/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat/outputs_variants/masselot_main_agnostic"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${DEST:-$HERE/outputs_variants/masselot_main_corrected}"

mkdir -p "$DEST"
echo "cities : $CITIES"
echo "mode   : $MODE"
echo "dest   : $DEST"
echo

# --exclude the uncertainty draws: 128 Monte Carlo realisations per city that no figure
# reads and that dominate the transfer. The CBA dashboard reads the summary CSVs, which
# live at the top level of tables/ and are kept.
EXCL=(--exclude 'uncertainty_improved_fast/' --exclude 'uncertainty_burke_sensitivity/' --exclude 'uncertainty_runs/')
INCL=(
  --include '*/'
  --include 'tables/**'
  --include 'interim/*.csv' --include 'interim/*.json'
  --include 'interim/*.npz' --include 'interim/*.gpkg'
  --include '*_muni_cov_yearly.csv' --include '*_fua.gpkg' --include 'lcz_masked_fua.tif'
)
[[ "$MODE" == "full" ]] && INCL+=(--include 'hazard/T2M_daily_mean_*_FUA_degC.nc')
INCL+=(--exclude '*')

for c in $CITIES; do
  echo "=== $c"
  rsync -az --info=progress2 --partial "${EXCL[@]}" "${INCL[@]}" \
    "$SSH_HOST:$REMOTE/$c/" "$DEST/$c/" \
    || { echo "  FAILED for $c - re-run to resume"; continue; }
  n=$(find "$DEST/$c" -type f | wc -l)
  echo "  $n files, $(du -sh "$DEST/$c" 2>/dev/null | cut -f1)"
done

echo
echo "next, from the repo root:"
echo "  cd gmd_visual_items"
echo "  python -m workflow_figures.make_figures --city rome --variant masselot_main_corrected"

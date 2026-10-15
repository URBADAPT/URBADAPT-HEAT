#!/usr/bin/env bash
# Pull the corrected output trees for the four GMD pilot cities, so the manuscript
# figures and results table can be rebuilt against the calibrated run.
#
# RUN THIS LOCALLY, in Git Bash, from urban-heat/ - not on JUNO. It opens one ssh
# connection to JUNO and streams the files back, so you authenticate once per run.
#
# Usage:
#   bash scripts/pull_gmd_pilots.sh [figures|full]
#
#   uncertainty  figures plus the uncertainty_*/ Monte Carlo draws, which
#            fig_uncertainty reads and nothing else does. No rasters.
#   figures  (default) everything the figure pipeline and the results table read:
#            tables/, the interim CSV/JSON/NPZ/GPKG grids, the FUA and LCZ geometry.
#            No hazard rasters, so the T2M map figure cannot be rebuilt from this mode.
#   full     the above plus hazard/T2M_daily_mean_*_FUA_degC.nc, which the T2M map
#            figure needs and which is the one product that visibly carries the ERA5
#            reference correction. Several hundred MB per city.
#
# WHY NOT rsync
# Git Bash on Windows ships no rsync, so the usual incantation fails before it starts.
# This uses the same ssh + tar stream as the other pull scripts in this directory.
#
# RESUMABLE, AND SIZE-CHECKED
# The local inventory (relative path + byte size) is sent up the same connection, and
# the remote skips any file whose size already matches locally. A file that is present
# but truncated - an interrupted earlier run - does NOT match, so it is re-sent rather
# than silently kept. A bare existence test would keep it, which is how ten truncated
# WorldPop rasters once got through.
#
# WHY A SEPARATE SCRIPT FROM pull_missing_cities.sh
# That one fetches two tables each for 40 cities; this fetches most of the tree for
# four. The uncertainty draws stay excluded in both: 128 realisations per city that no
# figure reads, and which the 40-city pull already brought down where needed.

set -uo pipefail

MODE="${1:-figures}"
case "$MODE" in figures|uncertainty|full) ;; *) echo "usage: bash scripts/pull_gmd_pilots.sh [figures|uncertainty|full]"; exit 2 ;; esac

CITIES="${CITIES:-rome athens lisbon copenhagen}"
SSH_HOST="${JUNO_HOST:-juno}"
REMOTE="${REMOTE:-/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat/outputs_variants/masselot_main_agnostic}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${DEST:-$HERE/outputs_variants/masselot_main_corrected}"

mkdir -p "$DEST"
echo "cities : $CITIES"
echo "mode   : $MODE"
echo "dest   : $DEST"

# Inventory of what is already here, as "<city>/<relative path><TAB><bytes>".
INV="$(mktemp)"; trap 'rm -f "$INV"' EXIT
# A sentinel so the inventory is NEVER empty. awk's NR==FNR idiom silently inverts on an
# empty first file - the wanted list becomes the "already have" set and nothing is sent -
# which on the first run means a clean exit 0 that transfers no files at all.
printf '__sentinel__\t-1\n' > "$INV"
for c in $CITIES; do
  [[ -d "$DEST/$c" ]] || continue
  ( cd "$DEST" && find "$c" -type f -printf '%p\t%s\n' 2>/dev/null ) >> "$INV"
done
echo "local  : $(( $(wc -l < "$INV") - 1 )) file(s) already present"
echo

# The find expression evaluated on the remote, per city directory.
# .npz is matched anywhere, not just under interim/: ac_coverage_maps_<city>.npz sits at
# the top of the tree, and an interim-only pattern silently costs the AC penetration map.
# The uncertainty_*/ subtrees are 128 Monte Carlo draws per city. They are excluded in
# `figures` mode because no figure reads them and across 40 cities they are gigabytes -
# but fig_uncertainty reads exactly those draws, so `uncertainty` and `full` keep them.
# For four pilot cities the cost is modest; do not lift it for a 40-city pull.
TABLES_EXPR='\( -path "*/tables/*" -not -path "*/uncertainty_*/*" \)'
[[ "$MODE" == "uncertainty" || "$MODE" == "full" ]] && TABLES_EXPR='-path "*/tables/*"'

FIND_EXPR="$TABLES_EXPR"' -o -name "*.npz" -o -name "*.png" -o -path "*/figures/*" -o \( -path "*/interim/*" \( -name "*.csv" -o -name "*.json" -o -name "*.gpkg" \) \) -o -name "*_muni_cov_yearly.csv" -o -name "*_fua.gpkg" -o -name "lcz_masked_fua.tif"'
[[ "$MODE" == "full" ]] && FIND_EXPR="$FIND_EXPR"' -o -name "T2M_daily_mean_*_FUA_degC.nc"'

< "$INV" ssh "$SSH_HOST" "
  set -e
  cat > /tmp/inv_\$\$.txt
  cd '$REMOTE'
  : > /tmp/want_\$\$.txt
  for c in $CITIES; do
    [ -d \"\$c\" ] || { echo \"  [remote] no tree for \$c\" >&2; continue; }
    find \"\$c\" -type f \\( $FIND_EXPR \\) -printf '%p\t%s\n' >> /tmp/want_\$\$.txt 2>/dev/null || true
  done
  # Keep only what is absent locally or differs in size.
  awk -F'\t' 'NR==FNR{have[\$1]=\$2; next} have[\$1]!=\$2 {print \$1}' \\
    /tmp/inv_\$\$.txt /tmp/want_\$\$.txt > /tmp/send_\$\$.txt
  echo \"  [remote] \$(wc -l < /tmp/want_\$\$.txt) candidate(s), sending \$(wc -l < /tmp/send_\$\$.txt)\" >&2
  tr '\n' '\0' < /tmp/send_\$\$.txt | tar czf - --null --no-recursion -T - 2>/dev/null
  rm -f /tmp/inv_\$\$.txt /tmp/want_\$\$.txt /tmp/send_\$\$.txt
" | tar xzf - -C "$DEST"
rc=${PIPESTATUS[1]}

echo
for c in $CITIES; do
  n=$(find "$DEST/$c" -type f 2>/dev/null | wc -l)
  printf '  %-12s %5s files  %s\n' "$c" "$n" "$(du -sh "$DEST/$c" 2>/dev/null | cut -f1)"
done
empty=$(find "$DEST" -type f -empty 2>/dev/null | wc -l)
(( empty > 0 )) && echo "WARNING: $empty zero-length file(s) - delete them and re-run"
echo "exit=$rc"

echo
echo "next, from the repo root:"
echo "  cd visual_items/pilot_cities"
echo "  python -m workflow_figures.make_figures --city rome --variant masselot_main_corrected"

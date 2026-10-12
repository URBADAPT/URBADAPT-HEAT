#!/usr/bin/env bash
# Pull ONLY the cities an arm is still missing, and only the tables that are read.
#
# Usage:
#   bash scripts/pull_missing_cities.sh <arm> [mode]
#
#   mode = tables      (default) every top-level table per city, NO uncertainty subdirs
#          minimal     just the two files the calibration comparison reads
#          full        everything under tables/, uncertainty draws included
#
#   e.g.  bash scripts/pull_missing_cities.sh variance
#         bash scripts/pull_missing_cities.sh variance minimal
#
# WHY THIS IS SO MUCH SMALLER THAN A WHOLE-ARM PULL
# tables/ holds uncertainty_improved_fast/ and uncertainty_burke_sensitivity/ - 128 Monte
# Carlo draws per city - which is most of an arm's ~3.7 GB and which nothing in the
# comparison, the figure pipeline or the manuscript tables reads. Restricting to
# -maxdepth 1 under tables/ drops them. Combined with only fetching cities that are
# actually absent, the remaining transfer is a few MB rather than gigabytes.
#
# A city counts as present when its baseline-deaths table exists locally; that is the
# file every downstream consumer needs first, so a city without it is unusable anyway.

set -uo pipefail

ARM="${1:-}"
MODE="${2:-tables}"
DEST_ROOT="${DEST_ROOT:-/c/Users/giaco/OneDrive - IIASA/Documents/juno}"
REMOTE="/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat/outputs_variants/masselot_main_agnostic"
SSH_HOST="${JUNO_HOST:-juno}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROSTER="$HERE/validation/reference_geographies.csv"

case "$ARM" in
  era5|variance|both|none) ;;
  *) echo "usage: bash scripts/pull_missing_cities.sh <era5|variance|both|none> [tables|minimal|full]"; exit 2 ;;
esac
case "$MODE" in tables|minimal|full) ;; *) echo "bad mode '$MODE'"; exit 2 ;; esac

DEST="$DEST_ROOT/arm_$ARM"
[[ "$ARM" == "none" ]] && DEST="${NONE_DEST:-$DEST}"
mkdir -p "$DEST"
[[ -f "$ROSTER" ]] || { echo "FATAL: roster not found at $ROSTER"; exit 1; }

# Roster minus what is already usable locally.
MISSING=$(mktemp); trap 'rm -f "$MISSING"' EXIT
tail -n +2 "$ROSTER" | cut -d, -f1 | grep -v '^$' | while read -r c; do
  [[ -f "$DEST/$c/tables/annual_heat_deaths_baseline_current_ac_$c.csv" ]] || echo "$c"
done | LC_ALL=C sort -u > "$MISSING"

n=$(wc -l < "$MISSING")
if [[ "$n" -eq 0 ]]; then
  echo "arm '$ARM' already has every city on the roster - nothing to pull"
  exit 0
fi
echo "arm '$ARM' is missing $n cities:"
tr '\n' ' ' < "$MISSING"; echo; echo
echo "mode '$MODE' -> fetching from $SSH_HOST ..."

# -maxdepth 1 is what excludes the uncertainty_* subdirectories.
case "$MODE" in
  minimal) FIND_EXPR="-maxdepth 1 -type f \\( -name \"annual_heat_deaths_baseline_current_ac_\$c.csv\" -o -name \"\${c}_policy_comparison.csv\" \\)" ;;
  tables)  FIND_EXPR="-maxdepth 1 -type f -size -50M" ;;
  full)    FIND_EXPR="-type f -size -50M" ;;
esac

< "$MISSING" ssh "$SSH_HOST" "
  set -e
  cat > /tmp/miss_\$\$.txt
  cd '$REMOTE'
  : > /tmp/list_\$\$.txt
  while read -r c; do
    [ -d \"\$c/tables\" ] || { echo \"  [remote] no tables/ for \$c\" >&2; continue; }
    find \"./\$c/tables\" $FIND_EXPR >> /tmp/list_\$\$.txt 2>/dev/null || true
    [ -f \"./\$c/interim/hazard_manifest.json\" ] && echo \"./\$c/interim/hazard_manifest.json\" >> /tmp/list_\$\$.txt
  done < /tmp/miss_\$\$.txt
  echo \"  [remote] sending \$(wc -l < /tmp/list_\$\$.txt) file(s)\" >&2
  sed 's|^\./||' /tmp/list_\$\$.txt | tr '\n' '\0' | tar czf - --null --no-recursion -T - 2>/dev/null
  rm -f /tmp/miss_\$\$.txt /tmp/list_\$\$.txt
" | tar xzf - -C "$DEST"
rc=$?

echo
still=0
while read -r c; do
  [[ -f "$DEST/$c/tables/annual_heat_deaths_baseline_current_ac_$c.csv" ]] || { still=$((still+1)); echo "  still missing: $c"; }
done < "$MISSING"
got=$(( n - still ))
echo
echo "arm '$ARM': $got of $n fetched, $still still missing (exit=$rc)"
empty=$(find "$DEST" -type f -empty 2>/dev/null | wc -l)
(( empty > 0 )) && echo "WARNING: $empty zero-length file(s) - delete them and re-run"
(( still > 0 )) && echo "re-run this command to retry the rest"

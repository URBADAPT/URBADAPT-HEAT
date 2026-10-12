#!/usr/bin/env bash
# Resumable pull of one calibration arm's per-city tables from JUNO.
#
# Usage:  bash scripts/pull_arm.sh <arm> [dest-root]
#   e.g.  bash scripts/pull_arm.sh variance
#
# Re-run it as often as you like: each pass transfers only the files you do not
# already have, so an interrupted download continues instead of restarting.
#
# WHY NOT rsync
# rsync is the obvious tool, but it is not in Git Bash on this machine and installing
# it just for this is overkill. The same resumability is available with tar and ssh:
# send the remote a list of what is already on disk locally, let it diff that against
# what it would have sent, and tar only the difference. Comparison is by PATH, not by
# size or checksum, so a file that arrived truncated is NOT re-fetched - see --verify.
#
# WHY A FILE LIST RATHER THAN A TIMESTAMP
# The arms all write into the same outputs_variants tree on JUNO and overwrite each
# other, so remote mtimes say when the last arm ran, not whether this arm's copy is
# already here. Only the local inventory is trustworthy.

set -uo pipefail

ARM="${1:-}"
DEST_ROOT="${2:-/c/Users/giaco/OneDrive - IIASA/Documents/juno}"
REMOTE="/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat/outputs_variants/masselot_main_agnostic"
SSH_HOST="${JUNO_HOST:-juno}"

case "$ARM" in
  era5|variance|both|none) DEST="$DEST_ROOT/arm_$ARM" ;;
  --verify) DEST="" ;;
  *) echo "usage: bash scripts/pull_arm.sh <era5|variance|both|none> [dest-root]"; exit 2 ;;
esac

# --verify <arm>: re-check sizes of what is already local, and delete anything
# zero-length so the next pull re-fetches it. Path-based resumption cannot detect a
# truncated file on its own.
if [[ "$ARM" == "--verify" ]]; then
  A="${2:-}"; D="${3:-/c/Users/giaco/OneDrive - IIASA/Documents/juno}/arm_$A"
  [[ -d "$D" ]] || { echo "no such dir: $D"; exit 1; }
  n=$(find "$D" -type f -empty -print -delete | wc -l)
  echo "removed $n zero-length file(s) from $D; re-run the pull to re-fetch them"
  exit 0
fi

mkdir -p "$DEST"

HAVE=$(mktemp); trap 'rm -f "$HAVE"' EXIT
( cd "$DEST" && find . -type f 2>/dev/null | sed 's|^\./||' | LC_ALL=C sort ) > "$HAVE"
echo "already local : $(wc -l < "$HAVE") file(s) in $DEST"

# The remote reads the inventory on stdin and streams back a tarball of the complement.
# Both lists are sorted with the same collation so comm can diff them.
echo "asking $SSH_HOST for what is missing ..."
< "$HAVE" ssh "$SSH_HOST" "
  set -e
  cat > /tmp/have_\$\$.txt
  cd '$REMOTE'
  find . \\( -path '*/tables/*' -o -name 'hazard_manifest.json' \\) -type f -size -50M \
    | sed 's|^\./||' | LC_ALL=C sort > /tmp/want_\$\$.txt
  comm -23 /tmp/want_\$\$.txt /tmp/have_\$\$.txt > /tmp/todo_\$\$.txt
  echo \"remote: \$(wc -l < /tmp/want_\$\$.txt) wanted, \$(wc -l < /tmp/todo_\$\$.txt) to send\" >&2
  tr '\n' '\0' < /tmp/todo_\$\$.txt | tar czf - --null --no-recursion -T - 2>/dev/null
  rm -f /tmp/have_\$\$.txt /tmp/want_\$\$.txt /tmp/todo_\$\$.txt
" | tar xzf - -C "$DEST"
rc=$?

echo
echo "arm '$ARM' now holds:"
echo "  city dirs : $(ls -d "$DEST"/*/ 2>/dev/null | wc -l)"
echo "  csv files : $(find "$DEST" -name '*.csv' 2>/dev/null | wc -l)"
echo "  manifests : $(find "$DEST" -name 'hazard_manifest.json' 2>/dev/null | wc -l)"
empty=$(find "$DEST" -type f -empty 2>/dev/null | wc -l)
(( empty > 0 )) && echo "  WARNING   : $empty zero-length file(s) - run: bash scripts/pull_arm.sh --verify $ARM"
echo
echo "re-run this command to fetch anything still missing (exit=$rc)"

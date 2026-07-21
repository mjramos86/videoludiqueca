#!/usr/bin/env bash
#
# Download all media referenced by the static site from the live WordPress
# origin into ./wp-content/uploads/... preserving paths, so GitHub Pages can
# serve them at the same absolute URLs the HTML uses.
#
# Run this ONCE from the repository root on a machine WITH internet access
# (the generation environment has no outbound network):
#
#     bash scripts/download_media.sh
#
# It reads scripts/media_manifest.txt (one "/wp-content/uploads/..." path per
# line) and fetches https://videoludique.ca<path> to ./<path>.
# Re-running skips files already present. When done, commit wp-content/.
set -u
ORIGIN="${ORIGIN:-https://videoludique.ca}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/scripts/media_manifest.txt"
UA="Mozilla/5.0 (compatible; videoludique-migration/1.0)"

if [ ! -f "$MANIFEST" ]; then echo "manifest not found: $MANIFEST"; exit 1; fi

total=$(grep -c . "$MANIFEST"); i=0; ok=0; skip=0; fail=0
while IFS= read -r path; do
  [ -z "$path" ] && continue
  i=$((i+1))
  dest="$ROOT${path}"
  if [ -f "$dest" ] && [ -s "$dest" ]; then skip=$((skip+1)); continue; fi
  mkdir -p "$(dirname "$dest")"
  if curl -fsSL -A "$UA" --retry 3 --retry-delay 2 -o "$dest" "${ORIGIN}${path}"; then
    ok=$((ok+1)); printf "[%d/%d] ok   %s\n" "$i" "$total" "$path"
  else
    fail=$((fail+1)); rm -f "$dest"; printf "[%d/%d] FAIL %s\n" "$i" "$total" "$path"
  fi
done < "$MANIFEST"
echo "----"
echo "downloaded=$ok skipped=$skip failed=$fail total=$total"
[ "$fail" -eq 0 ] || echo "Some files failed (they may have been deleted on the origin). Review the FAIL lines above."

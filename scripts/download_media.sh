#!/usr/bin/env bash
#
# Download all media referenced by the static site into ./wp-content/uploads/...
# preserving paths, so GitHub Pages serves them at the same absolute URLs the
# HTML uses.
#
# Why not just fetch from https://videoludique.ca ? Because that domain now
# resolves to this static GitHub Pages site — the old WordPress files are no
# longer served there. The originals still live on WordPress.com, and its
# Jetpack "Site Accelerator" CDN (i*.wp.com) keeps mirroring them regardless of
# where the videoludique.ca DNS points. So we try the CDN first, then a list of
# fallback origins. Each candidate is validated to be a real image (not an HTML
# 404 page) before it is kept.
#
#     bash scripts/download_media.sh
#     ORIGINS="https://host-a %s|https://host-b %s" bash scripts/download_media.sh
#
# It reads scripts/media_manifest.txt (one "/wp-content/uploads/..." path per
# line). Re-running skips files already present. When done, commit wp-content/.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/scripts/media_manifest.txt"
UA="Mozilla/5.0 (compatible; videoludique-migration/1.0)"

# Origin templates tried in order; "%s" is replaced by the /wp-content/... path.
# Override with the ORIGINS env var (entries separated by "|").
DEFAULT_ORIGINS='https://i0.wp.com/videoludique.ca%s|https://i1.wp.com/videoludique.ca%s|https://videoludique.ca%s'
IFS='|' read -r -a ORIGIN_TMPLS <<< "${ORIGINS:-$DEFAULT_ORIGINS}"

if [ ! -f "$MANIFEST" ]; then echo "manifest not found: $MANIFEST"; exit 1; fi

is_image() { file -b --mime-type "$1" 2>/dev/null | grep -q '^image/'; }

total=$(grep -c . "$MANIFEST"); i=0; ok=0; skip=0; fail=0
declare -A won  # which origin template served (for a summary)

while IFS= read -r path; do
  [ -z "$path" ] && continue
  i=$((i+1))
  dest="$ROOT${path}"
  if [ -f "$dest" ] && [ -s "$dest" ]; then skip=$((skip+1)); continue; fi
  mkdir -p "$(dirname "$dest")"
  got=""
  for tmpl in "${ORIGIN_TMPLS[@]}"; do
    # shellcheck disable=SC2059
    url=$(printf "$tmpl" "$path")
    if curl -fsSL -A "$UA" --max-time 60 --retry 2 --retry-delay 1 -o "$dest" "$url" \
        && [ -s "$dest" ] && is_image "$dest"; then
      got="$tmpl"; break
    fi
    rm -f "$dest"
  done
  if [ -n "$got" ]; then
    ok=$((ok+1)); won["$got"]=$(( ${won["$got"]:-0} + 1 ))
    printf "[%d/%d] ok   %s\n" "$i" "$total" "$path"
  else
    fail=$((fail+1)); printf "[%d/%d] FAIL %s\n" "$i" "$total" "$path"
  fi
done < "$MANIFEST"

echo "----"
echo "downloaded=$ok skipped=$skip failed=$fail total=$total"
for tmpl in "${!won[@]}"; do echo "  via ${won[$tmpl]}x  $tmpl"; done
[ "$fail" -eq 0 ] || echo "Some files failed on every origin — review the FAIL lines above."
# Never fail the build: a few missing images must not block the deploy.
exit 0

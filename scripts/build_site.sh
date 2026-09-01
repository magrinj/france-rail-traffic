#!/usr/bin/env bash
# Builds dist/: the site shell, without the data. Days are added afterwards by
# build_window.py under dist/data/<date>/, indexed by dist/data/days.json.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist/data
cp web/index.html dist/
# Cloudflare Pages headers. days.json and meta.json carry the build fingerprint
# and are revalidated often; .bin files are requested with ?v=<build>, so their
# URL changes on every publish and they can be cached indefinitely.
cat > dist/_headers <<'HDR'
/data/days.json
  Cache-Control: public, max-age=300, must-revalidate
/data/*/meta.json
  Cache-Control: public, max-age=300, must-revalidate
/data/*/*/*.bin
  Cache-Control: public, max-age=31536000, immutable
/index.html
  Cache-Control: public, max-age=300, must-revalidate
/data/*/reseau/*.bin
  Cache-Control: public, max-age=86400
HDR

# Network layer and service analysis are produced per day by build_window.py,
# under dist/data/<date>/. Nothing to copy here.
echo "dist/ prepare (coquille)"

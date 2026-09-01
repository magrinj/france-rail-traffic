#!/usr/bin/env bash
# Serves the assembled site. Multi-day content lives in dist/, produced by
# build_site.sh then build_window.py, so serving web/ alone is not enough.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f dist/data/days.json ] || {
  echo "dist/ est vide ou incomplet. Lancer d'abord :"
  echo "  bash scripts/build_site.sh && python scripts/build_window.py"
  exit 1
}
echo "http://localhost:${1:-8777}/"
exec .venv/bin/python -m http.server "${1:-8777}" --directory dist

#!/usr/bin/env bash
# Smoke test for tw-bake: run the one-shot baker and check its output files
# actually landed with sane shapes. Run from the repo root.
set -euo pipefail

cd "$(dirname "$0")/../../.."  # repo root (.claude/skills/run-tw-bake/ -> tw/)

OUT=unreal/Content/Map
rm -rf "$OUT"

cargo run -p tw-bake

fail=0
check() {
  local f=$1
  if [[ ! -s "$OUT/$f" ]]; then
    echo "MISSING or empty: $OUT/$f" >&2
    fail=1
  fi
}
check terrain.obj
check provinces.json
check province_borders.json
check rivers.json
check forests.json

python3 - "$OUT/provinces.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
provinces = d.get("provinces", d)
n = len(provinces) if isinstance(provinces, list) else len(provinces)
assert n == 12, f"expected 12 provinces, got {n}"
print(f"provinces.json: {n} provinces OK")
EOF

if [[ $fail -eq 0 ]]; then
  echo "SMOKE OK: all bake outputs present in $OUT"
else
  exit 1
fi

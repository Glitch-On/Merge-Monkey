#!/usr/bin/env bash
# Regenerates examples/demo-backport/ from the built-in demo repository.
# Usage: examples/run_demo_example.sh   (needs `pip install -e .` first)
set -euo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/demo-backport"
WORK="$(mktemp -d)/mm-demo"
export NO_COLOR=1
mkdir -p "$OUT"

run() {  # run <file> <command...>: record the command and its real output
  local file="$1"; shift
  { echo "\$ $*"; "$@" 2>&1 || true; echo; } >> "$OUT/$file"
}

rm -f "$OUT"/*.txt
git mm demo "$WORK" > /dev/null
cd "$WORK"

run 01-trace.txt          git mm trace hotfix-812 --no-ai
run 02-predict.txt        git mm predict --pick prod dev
run 02-predict.txt        git mm predict prod dev
run 03-conflict.txt       git cherry-pick -x hotfix-812
run 03-conflict.txt       cat settings.py
run 04-explain.txt        git mm explain --no-ai
run 05-resolve.txt        git mm resolve --prefer tested --no-ai
run 05-resolve.txt        git log -1
run 05-resolve.txt        cat settings.py
run 06-after.txt          git mm show HEAD
run 06-after.txt          git mm trace hotfix-812 --no-ai
run 06-after.txt          git mm mark HEAD --ci passed --by dev-ci
run 06-after.txt          git mm show HEAD
git mm report -o "$OUT/report.html" > /dev/null
git mm explain --json --no-ai > /dev/null 2>&1 || true
echo "wrote $OUT"

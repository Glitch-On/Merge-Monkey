#!/usr/bin/env bash
# Runs the demo backport with a real Gemma model behind Ollama and checks that
# Gemma's explanation and suggestion come back. Needs Ollama on $OLLAMA_HOST with
# $MM_MODEL pulled.
set -euo pipefail
export NO_COLOR=1
WORK="$(mktemp -d)/mm-demo"
OUT="${1:-gemma-output}"
mkdir -p "$OUT"

git mm demo "$WORK" > /dev/null
cd "$WORK"
git cherry-pick -x hotfix-812 > /dev/null 2>&1 || true

git mm explain | tee "$OUT/explain.txt"
git mm explain --json > "$OUT/explain.json"
python3 - "$OUT/explain.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
ai = data["files"][0]["hunks"][0]["ai"]
assert ai, "Gemma returned nothing"
assert ai["model"].startswith("gemma"), ai["model"]
assert ai["suggestion"] in ("ours", "theirs", "combine", "unsure"), ai
assert ai["summary"].strip(), "empty summary"
print(f"Gemma ({ai['model']}) suggested: {ai['suggestion']}")
PY

git mm resolve --prefer ai | tee "$OUT/resolve.txt"
git log -1 --format=%B | tee "$OUT/commit-message.txt"
cp settings.py "$OUT/settings.py"

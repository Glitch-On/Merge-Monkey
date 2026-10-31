#!/usr/bin/env bash
# Runs Merge Monkey on real backport history from Django and CPython.
#
# Usage: examples/run_real_example.sh <django-clone> <cpython-clone>
#
# Both arguments are local git clones you already have. The script makes no
# network calls. Expected branches:
#   django:  main and stable/6.1.x   ("Backport of <sha> from main" trailers)
#   cpython: main and a 3.x branch   ("(cherry picked from commit <sha>)" trailers)
set -euo pipefail
if [ $# -ne 2 ]; then
  echo "usage: $0 <django-clone> <cpython-clone>" >&2
  exit 1
fi
OUT="$(cd "$(dirname "$0")" && pwd)"
D="$(cd "$1" && pwd)"
C="$(cd "$2" && pwd)"
export NO_COLOR=1

record() {  # record <outfile> <repo dir> <command...>
  local file="$1" dir="$2"; shift 2
  { echo "\$ $*"; (cd "$dir" && "$@" 2>&1) || true; echo; } >> "$file"
}

# Django
git -C "$D" config mergemonkey.env.prod stable/6.1.x
git -C "$D" config mergemonkey.env.dev main
BACKPORT=$(git -C "$D" log stable/6.1.x --grep='Backport of' -1 --format=%H)
ORIGINAL=$(git -C "$D" log -1 --format=%B "$BACKPORT" | sed -n 's/^Backport of \([0-9a-f]*\) from main.*/\1/p')
F="$OUT/django/output.txt"; mkdir -p "$OUT/django"; : > "$F"
record "$F" "$D" git log -1 --format=%B "$BACKPORT"
record "$F" "$D" git mm trace "$ORIGINAL" --no-ai
record "$F" "$D" git mm show "$BACKPORT"
record "$F" "$D" git mm predict --pick main stable/6.1.x --limit 15

# CPython: newest local 3.x branch
BR=$(git -C "$C" for-each-ref --format='%(refname:short)' 'refs/heads/3.*' | sort -V | tail -1)
git -C "$C" config mergemonkey.env.prod "$BR"
git -C "$C" config mergemonkey.env.dev main
BACKPORT=$(git -C "$C" log "$BR" --grep='cherry picked from' -1 --format=%H)
ORIGINAL=$(git -C "$C" log -1 --format=%B "$BACKPORT" | sed -n 's/.*cherry picked from commit \([0-9a-f]*\).*/\1/p')
F="$OUT/cpython/output.txt"; mkdir -p "$OUT/cpython"; : > "$F"
record "$F" "$C" git log -1 --format=%B "$BACKPORT"
record "$F" "$C" git mm trace "$ORIGINAL" --no-ai
record "$F" "$C" git mm predict --pick main "$BR" --limit 10 -v
echo "wrote $OUT/django/output.txt and $OUT/cpython/output.txt"

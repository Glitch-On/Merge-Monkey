#!/usr/bin/env bash
# Fail if anything that looks like an API key, token or private key is tracked.
# The hackathon rule: no API keys or secrets in the repo, ever.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

patterns=(
  'sk-[A-Za-z0-9_-]{20,}'                  # OpenAI-style keys
  'sk-ant-[A-Za-z0-9_-]{20,}'              # Anthropic keys
  'gh[pousr]_[A-Za-z0-9]{30,}'             # GitHub tokens
  'github_pat_[A-Za-z0-9_]{30,}'           # GitHub fine-grained tokens
  'AIza[0-9A-Za-z_-]{35}'                  # Google / Gemini keys
  'hf_[A-Za-z0-9]{30,}'                    # Hugging Face tokens
  'AKIA[0-9A-Z]{16}'                       # AWS access keys
  'xox[baprs]-[A-Za-z0-9-]{10,}'           # Slack tokens
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'     # private keys
)

found=0
for p in "${patterns[@]}"; do
  if git grep -nIE -e "$p" -- ':!scripts/check_secrets.sh' ; then
    found=1
  fi
done
for f in $(git ls-files | grep -E '(^|/)\.env($|\.)|\.pem$|\.key$|id_rsa' || true); do
  echo "secret-looking file is tracked: $f"
  found=1
done

if [ "$found" -ne 0 ]; then
  echo "Possible secret found. Remove it before merging." >&2
  exit 1
fi
echo "No secrets found in $(git ls-files | wc -l | tr -d ' ') tracked files."

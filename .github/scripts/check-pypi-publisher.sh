#!/usr/bin/env bash
# Exits 0 and writes configured=true|false to $GITHUB_OUTPUT: whether PyPI has a trusted
# publisher matching this job (repository, workflow, environment). Any other failure of
# the token exchange is an error. Needs `permissions: id-token: write`.
set -euo pipefail

oidc=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=pypi" | jq -r .value)
body=$(mktemp)
code=$(curl -sS -o "$body" -w '%{http_code}' -X POST https://pypi.org/_/oidc/mint-token \
  -H 'Content-Type: application/json' -d "{\"token\": \"$oidc\"}")
error=$(jq -r '.errors[0].code // empty' "$body" 2>/dev/null || true)
detail=$(jq -r '.errors[0].description // empty' "$body" 2>/dev/null || true)
rm -f "$body" # on success it holds a short-lived upload token; it is never printed

if [ "$code" = "200" ]; then
  echo "configured=true" >>"$GITHUB_OUTPUT"
elif [ "$error" = "invalid-publisher" ]; then
  echo "configured=false" >>"$GITHUB_OUTPUT"
  echo "::notice::No PyPI trusted publisher matches this job, skipping the upload. See RELEASING.md. ($detail)"
else
  echo "::error::PyPI token exchange failed (HTTP $code, ${error:-unknown}): $detail"
  exit 1
fi

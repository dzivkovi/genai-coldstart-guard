#!/usr/bin/env bash
# Derive a short-lived Databricks OAuth token (machine-to-machine / service principal)
# from a client ID + secret, and export it as DATABRICKS_TOKEN for the app to use.
#
# Usage (note the leading "source" so the export lands in YOUR shell, not a subshell):
#   source scripts/get_databricks_token.sh
#
# Reads DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET from the
# environment, or from a local .env if present. The token is valid ~1 hour; re-run to refresh.
#
# Mechanism: OAuth 2.0 client-credentials grant against the workspace OIDC token endpoint
#   POST {host}/oidc/v1/token   (HTTP Basic auth = client_id:client_secret)
#   body: grant_type=client_credentials&scope=all-apis
# This is the same exchange the Databricks CLI/SDK does internally for M2M auth.

# Load .env for host + client credentials if they are not already exported.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

# --- Preferred where credentials already live in ~/.databrickscfg (e.g. sites that forbid PATs):
# let the Databricks CLI mint the token from a named profile. That profile holds the host plus the
# service-principal client_id/client_secret (M2M) OR an OAuth user login; the CLI derives a
# short-lived token from either, so nothing sensitive is copied into .env.
#   Usage:  source scripts/get_databricks_token.sh <profile>   (or set DATABRICKS_CONFIG_PROFILE)
_profile="${1:-${DATABRICKS_CONFIG_PROFILE:-}}"
if [ -n "${_profile}" ] && command -v databricks >/dev/null 2>&1; then
  _cli_json="$(databricks auth token -p "${_profile}" 2>/dev/null)"
  if command -v jq >/dev/null 2>&1; then
    _cli_token="$(printf '%s' "${_cli_json}" | jq -r '.access_token // empty')"
  else
    _cli_token="$(printf '%s' "${_cli_json}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)"
  fi
  if [ -n "${_cli_token}" ]; then
    export DATABRICKS_TOKEN="${_cli_token}"
    echo "[token] exported DATABRICKS_TOKEN from ~/.databrickscfg profile '${_profile}' (valid ~1h). Re-run to refresh."
    unset _profile _cli_json _cli_token
    return 0 2>/dev/null || exit 0
  fi
  echo "[token] profile '${_profile}' did not yield a token; falling back to explicit client_id/secret." >&2
fi
unset _profile _cli_json _cli_token 2>/dev/null || true

# --- Fallback: explicit service-principal credentials from .env / environment (OIDC exchange below).
: "${DATABRICKS_HOST:?set DATABRICKS_HOST (in .env or the environment)}"
: "${DATABRICKS_CLIENT_ID:?set DATABRICKS_CLIENT_ID (service principal application ID)}"
: "${DATABRICKS_CLIENT_SECRET:?set DATABRICKS_CLIENT_SECRET (service principal OAuth secret)}"

_host="${DATABRICKS_HOST%/}"

_resp="$(curl -sS --request POST "${_host}/oidc/v1/token" \
  --user "${DATABRICKS_CLIENT_ID}:${DATABRICKS_CLIENT_SECRET}" \
  --data "grant_type=client_credentials&scope=all-apis")"
_rc=$?
if [ "${_rc}" -ne 0 ]; then
  echo "[token] curl failed (exit ${_rc}) calling ${_host}/oidc/v1/token" >&2
  return 1 2>/dev/null || exit 1
fi

# Extract access_token (prefer jq; fall back to python3).
if command -v jq >/dev/null 2>&1; then
  _token="$(printf '%s' "${_resp}" | jq -r '.access_token // empty')"
else
  _token="$(printf '%s' "${_resp}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("access_token",""))' 2>/dev/null)"
fi

if [ -z "${_token}" ]; then
  echo "[token] no access_token in response (check host / client_id / secret / scope):" >&2
  echo "        ${_resp}" >&2
  return 1 2>/dev/null || exit 1
fi

export DATABRICKS_TOKEN="${_token}"
echo "[token] exported DATABRICKS_TOKEN (length ${#_token}); valid ~1h. Re-run to refresh."

# Clean up locals so a sourced shell is not left with the secret echoed around.
unset _host _resp _rc _token

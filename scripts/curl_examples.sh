#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"

call_route() {
  local route="$1"

  echo
  echo "=== $route ==="
  curl -s -X POST "$BASE_URL/agentservice/agent/chat" \
    -H "Content-Type: application/json" \
    -d "{
      \"conversation_id\": \"demo-${route}\",
      \"route\": \"${route}\",
      \"messages\": [{\"role\": \"user\", \"content\": \"Test ${route}\"}]
    }" | jq
}

call_route "mock:success_fast"
call_route "mock:success_slow"
call_route "mock:cold_start_timeout"
call_route "mock:databricks_stopped"
call_route "mock:databricks_updating"
call_route "mock:bad_request"
call_route "mock:auth_error"
call_route "mock:upstream_503"
call_route "mock:guardrail_blocked"
call_route "mock:no_grounding"

# Stateful cold start: poll this repeatedly to watch warming flip to ready.
call_route "mock:cold_start"

# State injection: drive any endpoint state through the real classifier.
call_route "mock:state:NOT_READY:NOT_UPDATING"  # -> stopped
call_route "mock:state:READY:NOT_UPDATING"      # -> ready
call_route "mock:state:READY:IN_PROGRESS"       # -> updating

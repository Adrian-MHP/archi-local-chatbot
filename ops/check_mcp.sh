#!/usr/bin/env bash
set -euo pipefail

MCP_URL="${1:-http://127.0.0.1:18090/mcp}"
TOKEN="${MCP_BEARER_TOKEN:-}"

AUTH_HEADER=()
if [[ -n "$TOKEN" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer $TOKEN")
fi

curl -sS -X POST "$MCP_URL" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check-mcp","version":"1.0"}}}'

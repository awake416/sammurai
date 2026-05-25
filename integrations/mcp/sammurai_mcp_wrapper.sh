#!/bin/bash
# MCP server wrapper - loads env vars from file

ENV_FILE="${HOME}/.config/sammurai/env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

exec /home/chhikv/.venv/bin/python /home/chhikv/ai/sammurai/integrations/mcp/sammurai_mcp_server.py "$@"

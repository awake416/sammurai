#!/bin/bash
# MCP server wrapper - loads env vars from file

ENV_FILE="${HOME}/.config/sammurai/env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

exec $HOME/.venv/bin/python $HOME/ai/sammurai/integrations/mcp/sammurai_mcp_server.py "$@"

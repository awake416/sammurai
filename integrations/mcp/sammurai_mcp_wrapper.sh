#!/bin/bash
# MCP wrapper for Hermes gateway stdio MCP client

export SAMMURAI_WIKI_PATH=/home/chhikv/sammurai-brain
export SAMMURAI_CONFIG=/home/chhikv/ai/sammurai/config.yaml

exec /home/chhikv/.venv/bin/python /home/chhikv/ai/sammurai/integrations/mcp/sammurai_mcp_server.py

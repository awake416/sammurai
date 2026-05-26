# Sammurai MCP Server

MCP (Model Context Protocol) server exposing sammurai cognee knowledge base to AI agents.

## Why MCP?

Hermes runs in Docker container. Direct Python execution (`query_brain.py`) doesn't work across container boundary. MCP server:
- Runs on host system
- Exposes tools via MCP protocol (stdio/SSE)
- Hermes calls MCP tools from Docker
- Works with any MCP-compatible agent

## Installation

### 1. Install MCP Python SDK

```bash
~/.venv/bin/pip install mcp pyyaml
```

### 2. Test Server

```bash
cd ~/ai/sammurai
~/.venv/bin/python integrations/mcp/sammurai_mcp_server.py
```

Should start and wait for stdio input.

### 3. Configure Hermes

Add MCP server via Hermes CLI (will auto-discover tools):

```bash
hermes mcp add sammurai \
  --command $HOME/.venv/bin/python \
  --args $HOME/ai/sammurai/integrations/mcp/sammurai_mcp_server.py
```

When prompted, select "Y" to enable all 3 tools.

Verify registration:
```bash
hermes mcp list
```

**Manual config** (alternative to CLI):

Edit `~/.hermes/config.yaml`, add at end:

```yaml
mcp_servers:
  sammurai:
    command: $HOME/.venv/bin/python
    args:
      - $HOME/ai/sammurai/integrations/mcp/sammurai_mcp_server.py
    enabled: true
    env:
      SAMMURAI_WIKI_PATH: $HOME/sammurai-brain
      SAMMURAI_CONFIG: $HOME/ai/sammurai/config.yaml
```

**Note:** Use absolute paths (not `~`).

### 4. Start New Session

MCP tools only available in new sessions:

```bash
# WhatsApp: send new message to start fresh session
# Or restart gateway
systemctl --user restart hermes-gateway.service
```

## Available Tools

### search_brain

Search sammurai knowledge base using cognee RAG (semantic + graph search).

**Parameters:**
- `query` (string, required): Natural language question
- `context_limit` (integer, optional): Max chars of context (default: 3000)

**Returns:** JSON with `context`, `sources`, `query`

**Example:**
```
Query: "What are my pending tasks?"
Response: {
  "context": "Retrieved tasks from wiki...",
  "sources": ["tasks.md"],
  "query": "What are my pending tasks?"
}
```

### read_wiki_file

Read a specific wiki markdown file by name.

**Parameters:**
- `filename` (string, required): Wiki filename (e.g., "tasks.md")

**Returns:** Full file content as text

**Example:**
```
filename: "cybersecurity_trends_2025_2026.md"
Response: "# Cybersecurity Trends 2025/2026\n\n..."
```

### list_wiki_files

List all available wiki files.

**Parameters:** None

**Returns:** JSON with `files` array and `count`

**Example:**
```
Response: {
  "files": ["tasks.md", "index.md", "log.md", ...],
  "count": 12
}
```

## Usage in Hermes

Once configured, ask Hermes:
- "Search my brain for pending tasks"
- "What's in my sammurai knowledge base about cybersecurity?"
- "Read tasks.md from my wiki"
- "List all my wiki files"

Hermes auto-calls MCP tools when relevant.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Hermes (Docker Container)                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Agent LLM                                          │    │
│  │  ↓                                                  │    │
│  │  MCP Client ──────────────────────────────────────┐│    │
│  └────────────────────────────────────────────────────┘│    │
└────────────────────────────────────────────────────────┼────┘
                                                         │
                                      stdio/SSE (MCP)    │
                                                         ↓
┌──────────────────────────────────────────────────────────────┐
│  Host System (WSL)                                           │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Sammurai MCP Server                               │     │
│  │  (sammurai_mcp_server.py)                          │     │
│  │  ↓                                                  │     │
│  │  CogneeStore                                       │     │
│  │  ↓                                                  │     │
│  │  ~/sammurai-brain/                                 │     │
│  │    - wiki/*.md (markdown files)                    │     │
│  │    - .data/ (cognee index)                         │     │
│  └────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

## Troubleshooting

### Server won't start

Check dependencies:
```bash
~/.venv/bin/pip install mcp pyyaml
~/.venv/bin/python -c "import mcp, yaml, cognee; print('OK')"
```

### Hermes can't connect

Check Hermes MCP config:
```bash
cat ~/.hermes/mcp_servers.json
```

Verify paths are absolute (no `~`).

Check Hermes logs:
```bash
journalctl --user -u hermes-gateway.service -f | grep mcp
```

### No results from search_brain

Ensure cognee index exists:
```bash
ls ~/sammurai-brain/.data/
```

Rebuild if needed:
```bash
cd ~/ai/sammurai
~/.venv/bin/python -m src.backend.digest_runner
```

### Permission denied

Make script executable:
```bash
chmod +x ~/ai/sammurai/integrations/mcp/sammurai_mcp_server.py
```

## Testing Manually

Use MCP Inspector:

```bash
npx @modelcontextprotocol/inspector ~/ai/sammurai/integrations/mcp/sammurai_mcp_server.py
```

Or test with stdio:

```bash
echo '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}' | \
  ~/.venv/bin/python integrations/mcp/sammurai_mcp_server.py
```

## Environment Variables

- `SAMMURAI_WIKI_PATH`: Path to wiki (default: `~/sammurai-brain`)
- `SAMMURAI_CONFIG`: Path to config.yaml (default: `~/ai/sammurai/config.yaml`)

Set in Hermes MCP config or shell:
```bash
export SAMMURAI_WIKI_PATH=$HOME/sammurai-brain
```

## Logging

Server logs to stderr. View in Hermes logs:

```bash
journalctl --user -u hermes-gateway.service -f | grep sammurai-mcp
```

Or run manually to see logs:
```bash
~/.venv/bin/python integrations/mcp/sammurai_mcp_server.py
```

## Development

Server uses `mcp` Python SDK. Protocol: https://modelcontextprotocol.io/

Add new tools by:
1. Define in `list_tools()`
2. Implement handler in `call_tool()`
3. Test with MCP Inspector

## Related

- MCP Specification: https://modelcontextprotocol.io/
- Hermes MCP docs: Check Hermes documentation
- Skill integration (alternative): `../hermes/`

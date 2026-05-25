# Hermes Integration

Integration of Sammurai knowledge base with [Nous Research Hermes](https://github.com/NousResearch/hermes) agent.

## Architecture

```
WhatsApp Messages → wacli DB → Sammurai Pipeline
                                      ↓
                              digest_runner.py
                                      ↓
                    ┌─────────────────┴──────────────────┐
                    ↓                                    ↓
           Wiki Compiler (wiki/*.md)          Cognee Store (RAG index)
                    ↓                                    ↓
                    └──────────────────┬─────────────────┘
                                      ↓
                              Hermes Skill (query_brain.py)
                                      ↓
                            Nous Hermes Agent → WhatsApp
```

**Components:**
- **Sammurai** = backend (extraction → wiki compilation → cognee indexing)
- **Hermes** = frontend (queries cognee + internet search)

## Setup

### 1. Install Sammurai Skill

Copy skill to Hermes skills directory:

```bash
cp -r integrations/hermes ~/.hermes/skills/productivity/sammurai
chmod +x ~/.hermes/skills/productivity/sammurai/scripts/query_brain.py
```

### 2. Configure Paths

Skill expects:
- Sammurai repo: `~/ai/sammurai`
- Python venv: `~/.venv/bin/python3`
- Wiki location: `~/sammurai-brain`

Edit `scripts/query_brain.py` if paths differ.

### 3. Restart Hermes

```bash
systemctl --user restart hermes-gateway.service
```

### 4. Verify Installation

Test skill directly:

```bash
~/.hermes/skills/productivity/sammurai/scripts/query_brain.py "What are my pending tasks?"
```

Expected output:
```json
{
  "context": "Retrieved tasks from wiki...",
  "sources": ["tasks.md"]
}
```

## Usage

Ask Hermes via WhatsApp:
- "Query my brain about pending tasks"
- "Check sammurai wiki for cybersecurity trends"
- "What's in my second brain about SRP infrastructure?"

Hermes auto-discovers skills from `~/.hermes/skills/`. Use trigger phrases like "brain", "wiki", "sammurai" for better routing.

## Skill Details

**Location:** `~/.hermes/skills/productivity/sammurai/`

**Files:**
- `SKILL.md` — skill manifest
- `scripts/query_brain.py` — cognee query bridge

**How it works:**
1. Loads cognee store from `~/sammurai-brain`
2. Performs semantic + graph search
3. Returns context (max 3000 chars) + source files
4. Hermes LLM synthesizes final answer

## Maintenance

### Rebuild Index

After new WhatsApp messages digested:

```bash
cd ~/ai/sammurai
~/.venv/bin/python -m src.backend.digest_runner
```

This:
1. Extracts digest from configured groups
2. Compiles wiki markdown files
3. Rebuilds cognee index
4. Git commits changes

### Manual Digest Extraction

Pull last 60 days, 3000 messages:

```bash
~/.venv/bin/python -m src.backend.cli --all --use-llm --digest --days 60 --limit 3000 > ~/sammurai-brain/raw/digest_$(date +%Y-%m-%d).txt
```

Then rebuild:

```bash
~/.venv/bin/python -m src.backend.digest_runner
```

### Automated Daily Digests

Systemd timer already configured:

```bash
systemctl --user status sammurai-digest.timer
systemctl --user list-timers sammurai-digest.timer
```

Runs daily at 23:59, extracts last 1 day of messages.

## Troubleshooting

### Skill Not Found

Check Hermes skill discovery:
```bash
ls ~/.hermes/skills/productivity/sammurai/
```

Should show: `SKILL.md`, `scripts/`

### Import Errors

Ensure sammurai venv active:
```bash
~/.venv/bin/python -c "import cognee; print('OK')"
```

If fails, reinstall:
```bash
cd ~/ai/sammurai
~/.venv/bin/pip install -r requirements.txt
```

### Empty Results

Check cognee index exists:
```bash
ls ~/sammurai-brain/.data/
```

If empty, rebuild:
```bash
cd ~/ai/sammurai
~/.venv/bin/python -m src.backend.digest_runner
```

### Check Hermes Logs

```bash
journalctl --user -u hermes-gateway.service -f
```

## MCP Server (Recommended for Docker/WSL)

**Use MCP server if Hermes runs in Docker** (skill won't work across container boundary).

See `../mcp/README.md` for full MCP setup.

**Quick setup:**

```bash
# Install MCP SDK
~/.venv/bin/pip install mcp

# Add to Hermes MCP config (~/.hermes/mcp_servers.json):
# Use absolute paths (no ~), Docker may not expand them
{
  "mcpServers": {
    "sammurai": {
      "command": "/home/chhikv/.venv/bin/python",
      "args": ["/home/chhikv/ai/sammurai/integrations/mcp/sammurai_mcp_server.py"],
      "env": {
        "SAMMURAI_WIKI_PATH": "/home/chhikv/sammurai-brain",
        "SAMMURAI_CONFIG": "/home/chhikv/ai/sammurai/config.yaml"
      }
    }
  }
}

# Restart Hermes
systemctl --user restart hermes-gateway.service
```

**MCP Tools:**
- `search_brain` — semantic + graph search of knowledge base
- `read_wiki_file` — read specific wiki file
- `list_wiki_files` — list all available files

## Related

- Main README: `../../README.md`
- Sammurai architecture: `../../docs/architecture/`
- Wiki compiler: `../../src/backend/wiki_compiler.py`
- Cognee store: `../../src/backend/cognee_store.py`

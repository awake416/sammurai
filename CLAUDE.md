# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Sammurai — WhatsApp Second Brain. Extracts topics/action items from wacli SQLite DB, compiles markdown wiki, builds cognee RAG index, integrates with Nous Research Hermes agent for WhatsApp queries.

## Commands

```bash
# Extract & Digest
python -m src.backend.cli --list                    # List groups
python -m src.backend.cli "Group Name" --days 7     # Extract from group
python -m src.backend.cli --all --use-llm --digest  # Full digest, all groups

# Wiki Pipeline (extract → compile → index)
python -m src.backend.digest_runner                 # Full pipeline: digest → wiki → cognee

# Brain Init
python -m src.backend.brain_init                    # Scaffold ~/sammurai-brain/

# Hermes Integration
~/.hermes/skills/productivity/sammurai/scripts/query_brain.py "What tasks pending?"

# Tests
pytest --cov=src/backend --cov-report=xml           # All tests + coverage
pytest tests/test_models.py::test_function_name     # Single test
pytest -k "pattern"                                 # Pattern match

# Lint
ruff check src/
ruff format src/

# Docker
make up          # Start with secret injection
make down        # Stop
make build       # Rebuild no-cache
make test        # Run tests in container
make health      # Check /health endpoint

# Systemd Services
systemctl --user status sammurai-agent.service      # Agent daemon
systemctl --user status sammurai-digest.timer       # Daily digest timer
```

## Architecture

**Data flow:** wacli SQLite DB → `database.py` → enriched messages → `llm_client.py`/`parser.py` → `topic_extractor.py` → `digest_runner.py` → wiki compile + cognee index → Hermes agent queries

**Pipeline stages:**
1. **Extraction** — CLI pulls messages from wacli DB
2. **Enrichment** — `rich_document_parser.py` + `url_extractor.py` add document/URL content
3. **Processing** — LLM or rule-based parser extracts action items
4. **Digest** — `topic_extractor.py` clusters topics, generates digest
5. **Wiki** — `wiki_compiler.py` updates markdown files in `~/sammurai-brain/wiki/`
6. **Index** — `cognee_store.py` rebuilds RAG index (semantic + graph)
7. **Query** — Hermes skill calls cognee for knowledge retrieval

Key modules in `src/backend/`:
- `cli.py` — argparse CLI, orchestrates extraction pipeline, parallel group processing via ThreadPoolExecutor
- `database.py` — reads from wacli's SQLite DB (`~/.wacli/wacli.db`)
- `llm_client.py` — LiteLLM wrapper, batch extraction with parallel batches
- `parser.py` — rule-based fallback extractor (no LLM needed)
- `topic_extractor.py` — topic clustering, digest generation, document summarization
- `rich_document_parser.py` — PDF/image text extraction via OCR
- `url_extractor.py` — bulk URL fetching + summarization
- `wiki_compiler.py` — maintains structured markdown wiki
- `cognee_store.py` — cognee RAG index (semantic + graph search)
- `digest_runner.py` — full pipeline orchestrator (CLI → digest → wiki → index)
- `agent_daemon.py` — standalone agent (polls WhatsApp, routes to hermes)
- `hermes_agent.py` — read-only query agent for wiki
- `intent_router.py` — classify query vs capture intent
- `models.py` — Pydantic v2 models: `Message`, `ActionableItem`, `DocumentSummary`, enums
- `utils.py` — PII redaction, SSRF protection, URL validation

**Dual extraction strategy:** Config `parser.use_llm` controls default. `--use-llm` forces LLM (fails hard). `--no-llm` forces rule-based. When LLM is default and fails, falls back to rule-based if `parser.fallback_to_rule_based` is true.

**Parallel processing:** Groups processed concurrently (`parallel.workers`), batches within groups also concurrent (`parallel.batch_workers`).

## Technical Constraints

- Python 3.12+, Pydantic v2+
- Ruff for linting (line-length 88, Google docstring convention)
- LLM provider configured via env vars: `LITELLM_BASE_URL`, `LITELLM_API_KEY`
- Default model: `claude-sonnet-4.6` (set in config.yaml)
- Cognee 1.1.0+ for RAG indexing
- Wiki location: `~/sammurai-brain/` (Git repo for versioning)
- Hermes integration: skill at `~/.hermes/skills/productivity/sammurai/`
- Security: HTTPS enforcement, SSRF protection (IP validation), PII redaction in logs, path traversal prevention on db-path
- Conventional commits: `type(scope): description`
- Value hierarchy: Accuracy > Privacy > UI Consistency > Speed

## Hermes Integration

**Architecture:** Sammurai (backend) + Hermes (frontend) + MCP bridge
- Sammurai extracts/compiles/indexes knowledge base
- Hermes (Docker) queries via MCP server (host)
- MCP server wraps cognee store

**Files:**
- `integrations/mcp/` — **MCP server (recommended for Docker/WSL)**
  - `sammurai_mcp_server.py` — stdio MCP server
  - `hermes_mcp_config.json` — Hermes config template
  - Tools: `search_brain`, `read_wiki_file`, `list_wiki_files`
- `integrations/hermes/` — Python skill (native execution only)
  - `scripts/query_brain.py` — direct cognee bridge
  - Works only if Hermes runs on host (not Docker)

**MCP Server Setup:**
```bash
~/.venv/bin/pip install mcp
# Add to ~/.hermes/mcp_servers.json (use absolute paths)
systemctl --user restart hermes-gateway.service
```

**Usage:** Ask Hermes "Search my brain for X" → MCP call → cognee → return context

See `integrations/mcp/README.md` (Docker) or `integrations/hermes/README.md` (native) for setup.

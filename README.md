# Sammurai — Your Local Second Brain

Sammurai is a **local-first, Git-backed Second Brain**. It passively ingests everything that flows through your messaging and inbox, autonomously compiles it into a structured Markdown knowledge base, indexes it for semantic + graph search, and lets you query it in natural language through a conversational AI agent.

You never file, tag, or organize a note. Messages and emails go in; an interconnected, version-controlled wiki of topics, tasks, and concept pages comes out — and stays on your machine.

**Core philosophy:** zero-friction capture · autonomous organization · private, local-first execution.

## How It Works

```
   INTEGRATIONS              THE BRAIN                      QUERY
  (ingestion sources)   (local, Git-backed)            (read-only)

  ┌──────────────┐
  │  WhatsApp    │──┐
  │  (wacli)     │  │     ┌──────────────────────┐     ┌──────────────┐
  └──────────────┘  │     │   Digest Pipeline    │     │ Hermes Agent │
                    ├────▶│  parse → enrich →    │────▶│  search_brain│
  ┌──────────────┐  │     │  extract → digest    │     │  read_wiki   │
  │  Email       │──┘     └──────────┬───────────┘     │  (via MCP)   │
  │  (Gmail API) │                   │                 └──────┬───────┘
  └──────────────┘                   ▼                        │
                          ┌──────────────────────┐            ▼
                          │  ~/sammurai-brain/    │       back to you
                          │   wiki/  (Markdown)   │      (WhatsApp/chat)
                          │   raw/   (digests)    │
                          │   .git/  (versioned)  │◀───── cognee RAG index
                          └──────────────────────┘       (semantic + graph)
```

Every integration funnels into **one** pipeline:

1. **Ingest** — background services sync messages/emails into local SQLite DBs.
2. **Extract** — an LLM (via LiteLLM) pulls topics, action items, and document/URL summaries.
3. **Digest** — daily output is written to `raw/digest_YYYY-MM-DD.txt`.
4. **Compile** — the wiki compiler updates `tasks.md`, concept pages, and `index.md`, then `git commit`s the change (so an LLM mistake can never silently destroy history).
5. **Index** — cognee rebuilds the RAG index (semantic + graph) over the wiki.
6. **Query** — Hermes answers natural-language questions, citing only what it retrieves.

## The Brain (`~/sammurai-brain/`)

A plain directory under Git version control — no proprietary format, no cloud:

| Path | Contents |
|------|----------|
| `wiki/index.md` | Knowledge index, links to every concept page |
| `wiki/tasks.md` | Aggregated action items (priority, category, deadline, source) |
| `wiki/log.md` | Append-only log of every compilation run |
| `wiki/*.md` | Auto-created/updated concept pages (e.g. `srp_parking_management.md`) |
| `raw/` | Raw daily digests before compilation |
| `.git/` | Full history — every digest is a commit |

Scaffold a fresh brain with:

```bash
python -m src.backend.brain_init
```

## Integrations

Sources are interchangeable adapters. Each writes to a local SQLite DB that the digest pipeline reads from. You can run one, both, or add more.

### WhatsApp (via `wacli`)

`wacli` syncs your WhatsApp into `~/.wacli/wacli.db` as a background service. See [WhatsApp Sync Setup](#whatsapp-sync-setup) below.

### Email (via Gmail API)

The `emailsync` daemon polls Gmail and writes important mail into `~/.emailsync/*.db`, applying a domain allowlist + keyword/LLM subject classifier so only signal (bills, statements, notices) reaches the brain. See [Email Sync Setup](#email-sync-setup) below.

## Dual Brains (Personal / Work)

Sammurai supports isolated brains driven by separate config files, so work and personal knowledge never mix:

| Config | Brain / dataset | Email DB | Domain focus |
|--------|-----------------|----------|--------------|
| `config-personal.yaml` | personal wiki + cognee dataset | `~/.emailsync/email-personal.db` | banks, MyGate, community |
| `config-work.yaml` | work wiki + cognee dataset | `~/.emailsync/email-work.db` | your employer's domain |
| `config.yaml` | default | `~/.emailsync/email.db` | base/shared |

Each has its own systemd digest timer (`sammurai-digest-personal.timer`, `sammurai-digest-work.timer`) and its own cognee dataset (set via `wiki.dataset_name`).

## Quick Start

### Prerequisites

- **Python 3.12+**
- **SQLite3** (bundled with Python)
- At least one integration: **wacli** (WhatsApp) and/or a **Gmail account** with API access
- An LLM endpoint reachable via **LiteLLM**
- **cognee 1.1.0+** for RAG indexing

### Installation

```bash
git clone https://github.com/yourusername/sammurai.git
cd sammurai

python3 -m venv ~/.venv/sammurai
source ~/.venv/sammurai/bin/activate
pip3 install -r requirements.txt
```

### Configure your LLM provider

```bash
export LITELLM_BASE_URL="https://your-litellm-url.com"
export LITELLM_API_KEY="your-api-key"
```

Then edit `config.yaml` (model, confidence threshold, batch sizes, groups, parallelism). Default model: `claude-sonnet-4.6`.

### Run the full pipeline

```bash
# Ingest → digest → compile wiki → rebuild cognee index
python -m src.backend.digest_runner

# Look back further (e.g. backfill 7 days of history)
python -m src.backend.digest_runner --days 7

# Run a specific brain
python -m src.backend.digest_runner --config config-work.yaml
```

## Integration Setup

### WhatsApp Sync Setup

Sammurai reads from the local `wacli` database. Keep it current with a systemd user service:

1. **Install wacli** (requires Go and Homebrew):
   ```bash
   brew install steipete/tap/wacli
   ```

   If you hit a "Client outdated (405)" error, rebuild with the latest whatsmeow:
   ```bash
   git clone https://github.com/steipete/wacli.git /tmp/wacli-build
   cd /tmp/wacli-build
   go get go.mau.fi/whatsmeow@latest && go mod tidy
   CGO_CFLAGS="-DSQLITE_ENABLE_FTS5" CGO_LDFLAGS="-lm" go build -o wacli ./cmd/wacli
   sudo cp wacli "$(which wacli)"
   ```

2. **Authenticate** (one-time — scan the QR code with your phone):
   ```bash
   wacli auth
   ```

3. **Create the systemd user service**:
   ```bash
   mkdir -p ~/.config/systemd/user
   cat > ~/.config/systemd/user/wacli-sync.service << 'EOF'
   [Unit]
   Description=WhatsApp CLI Sync
   After=network-online.target

   [Service]
   ExecStart=/home/linuxbrew/.linuxbrew/bin/wacli sync --download-media --follow --refresh-contacts --refresh-groups
   Restart=on-failure
   RestartSec=10

   [Install]
   WantedBy=default.target
   EOF
   ```

4. **Enable and start**:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now wacli-sync.service
   ```

5. **Useful commands**:
   ```bash
   systemctl --user status wacli-sync    # Check status
   journalctl --user -u wacli-sync -f    # Tail logs
   systemctl --user restart wacli-sync   # Restart
   ```

6. **Backfill historical messages** (stop the service first to release the DB lock):
   ```bash
   systemctl --user stop wacli-sync
   wacli history backfill --chat <JID> --count 50 --requests 5
   systemctl --user start wacli-sync
   ```
   Find chat JIDs with `wacli chats list`.

### Email Sync Setup

The `emailsync` daemon polls Gmail via the API (read-only scope) and writes filtered, important mail into a local SQLite DB.

1. **Create OAuth credentials** in the [Google Cloud Console](https://console.cloud.google.com/): enable the Gmail API, create an **OAuth client ID** of type *Desktop app*, and download the JSON to `~/.emailsync/credentials.json`.

2. **Authorize** (one-time, generates the refresh token):
   ```bash
   python scripts/reauth_gmail.py
   ```
   This prints a consent URL and listens on `localhost:8765`. Open the URL, approve read-only access, and the resulting token is written to `~/.emailsync/token.json` (mode `0600`). The work and personal daemons share this single token (same Google account).

   > **WSL note:** `reauth_gmail.py` deliberately does **not** auto-launch a browser (headless boxes have none). Open the printed URL in your host browser; the `localhost:8765` redirect is delivered back through WSL2 port forwarding.

3. **Install the systemd services** (one per brain):
   ```bash
   systemctl --user enable --now emailsync.service           # default
   systemctl --user enable --now emailsync-personal.service  # personal brain
   ```

4. **Verify it's syncing**:
   ```bash
   systemctl --user status emailsync.service
   journalctl --user -u emailsync.service -f   # look for "Synced N messages"
   ```

The daemon refreshes its access token automatically. If the **refresh token** is ever revoked or expires (symptom: the service crash-loops with `webbrowser.Error: could not locate runnable browser`), just re-run `python scripts/reauth_gmail.py` and restart the services.

**Email filtering** (in each config's `email` block):
- `from_filters` — domain allowlist; only mail from these domains reaches the digest.
- `subject_filters` — fast keyword include/exclude plus an optional LLM classifier (`use_llm_classifier`) for semantic importance.

## Configuration Reference

### config.yaml Structure

```yaml
llm:
  model: "claude-sonnet-4.6"   # LiteLLM-routed model
  confidence_threshold: 0.75   # drop low-confidence extractions
  batch_size: 10

database:
  path: "~/.wacli/wacli.db"    # WhatsApp source DB

parser:
  use_llm: true                # false = rule-based only
  fallback_to_rule_based: true # fall back if the LLM fails

parallel:
  groups:                      # WhatsApp group JIDs to process
    - "GROUP_ID_1"
    - "GROUP_ID_2"
  workers: 5                   # concurrent groups
  batch_workers: 3             # concurrent batches per group

wiki:
  path: "~/sammurai-brain"     # the Brain (Git repo)
  schema: "SCHEMA.md"          # rules governing wiki structure
  dataset_name: "sammurai_wiki" # cognee dataset (isolate per brain)

cron:
  days: 1                      # look-back window per digest run

email:
  enabled: true
  database:
    path: "~/.emailsync/email.db"
  sync:
    poll_interval: 300
    max_results_per_sync: 100
    labels_to_sync: ["INBOX", "IMPORTANT"]
    skip_labels: ["SPAM", "[Gmail]/Sent Mail", "[Gmail]/Trash"]
    from_filters: ["mygate.com", "axisbank.com"]   # domain allowlist
    subject_filters:
      use_llm_classifier: true
      include_keywords: ["statement", "notice", "certificate"]
      exclude_keywords: ["offer", "emi", "scam"]
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LITELLM_BASE_URL` | Base URL for LiteLLM proxy or custom endpoint |
| `LITELLM_API_KEY` | API key for your LLM provider |
| `OPENAI_API_KEY` | OpenAI API key (if using OpenAI directly) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude directly) |

## Querying Your Brain (Hermes Integration)

Sammurai is the **backend** (ingest → compile → index). [Nous Research Hermes](https://github.com/NousResearch/hermes) is the **frontend** — a conversational agent that queries the brain and answers, citing only what it retrieves. An MCP server bridges the two.

> The agent is **read-only by design** — it has no file-writing or deleting tools, so a query can never corrupt the Git-backed wiki.

### Option A: MCP Server (recommended for Docker/WSL)

Use MCP when Hermes runs in Docker and can't execute host Python directly.

```bash
~/.venv/bin/pip install mcp

hermes mcp add sammurai \
  --command $HOME/.venv/bin/python \
  --args $HOME/ai/sammurai/integrations/mcp/sammurai_mcp_server.py

hermes mcp list   # verify; tools appear in new sessions
```

**MCP Tools:**
- `search_brain` — semantic + graph search over the wiki
- `read_wiki_file` — read a specific file
- `list_wiki_files` — list all files

See [integrations/mcp/README.md](integrations/mcp/README.md).

### Option B: Python Skill (native host execution)

```bash
cp -r integrations/hermes ~/.hermes/skills/productivity/sammurai
chmod +x ~/.hermes/skills/productivity/sammurai/scripts/query_brain.py
systemctl --user restart hermes-gateway.service
```

See [integrations/hermes/README.md](integrations/hermes/README.md).

### Example queries

- "Search my brain for pending tasks"
- "What did we discuss about parking this week?"
- "When is my daughter's football class?"
- "Read tasks.md from my wiki"

## Automated Updates

Each brain has a systemd digest timer:

```bash
systemctl --user status sammurai-digest.timer            # default
systemctl --user status sammurai-digest-personal.timer   # personal
systemctl --user status sammurai-digest-work.timer       # work
```

Or trigger a run manually:

```bash
python -m src.backend.digest_runner --config config-personal.yaml
```

## Development

### Tests

```bash
pytest --cov=src/backend --cov-report=xml          # all tests + coverage
pytest tests/test_models.py::test_function_name    # single test
pytest -k "pattern"                                # pattern match
```

### Linting

```bash
ruff check src/
ruff format src/
```

## Security & Privacy

Local-first is the whole point — your data does not leave your machine.

- **Local-first storage** — `wacli.db`, `email*.db`, and the Markdown `wiki/` never upload to cloud storage or a hosted vector DB.
- **Read-only agent** — Hermes is denied any write/delete tools; it cannot corrupt the Git-backed wiki.
- **Git as a safety net** — every compilation commits, so a bad LLM run is recoverable.
- **Minimal LLM context** — only the necessary chunks are sent to the model, never whole databases.
- **Read-only email scope** — Gmail OAuth requests only `gmail.readonly`; the token is stored `0600`.
- **HTTPS enforcement & SSRF protection** — external URLs must be HTTPS; resolved IPs are checked against private/internal ranges, with DNS-rebinding prevention on document fetches.
- **PII redaction** — sensitive data is redacted from logs.
- **Secrets in env, not config** — API keys live in environment variables.

## License

MIT License — see LICENSE file for details.

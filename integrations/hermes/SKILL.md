---
name: sammurai
description: "Sammurai: query Second Brain wiki built from WhatsApp messages"
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
prerequisites:
  commands: [python3]
metadata:
  hermes:
    tags: [Knowledge, Wiki, RAG, Cognee, Second Brain, WhatsApp]
---

# Sammurai — Second Brain Query

Query the sammurai wiki compiled from WhatsApp messages. Uses cognee for semantic + graph search.

## Setup

No setup needed. Script uses sammurai venv at `~/.venv` and reads from `~/sammurai-brain`.

## Usage

Query the brain:

```bash
SCRIPT=~/.hermes/skills/productivity/sammurai/scripts/query_brain.py
python3 "$SCRIPT" "What are the pending tasks?"
python3 "$SCRIPT" "Tell me about SRP infrastructure issues"
python3 "$SCRIPT" "What's in the cybersecurity trends page?"
```

Returns relevant context from wiki with sources.

## How It Works

1. Queries cognee semantic + graph index
2. Returns context up to 3000 chars
3. Cites source files from wiki

## Files

- `~/sammurai-brain/wiki/*.md` — compiled wiki pages
- `~/sammurai-brain/raw/digest_*.txt` — raw digest dumps
- Cognee index automatically loaded from `~/sammurai-brain/.data/`

## Maintenance

Rebuild index after new digests:

```bash
cd ~/ai/sammurai
~/.venv/bin/python -m src.backend.digest_runner
```

## API

Script outputs JSON:

```json
{
  "context": "Retrieved context from wiki...",
  "sources": ["tasks.md", "cybersecurity_trends_2025_2026.md"]
}
```

If no results: `{"context": "", "sources": []}`

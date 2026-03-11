# WhatsApp Action Item Extractor - Specification

## Project Overview

**Project Name:** WhatsApp Action Item Extractor  
**Project Type:** CLI Tool (Python)  
**Core Functionality:** Extract action items from WhatsApp group messages using wacli's SQLite database with hybrid rule-based and LLM-based parsing.

## Architecture

### Data Flow
```
wacli (WhatsApp Sync) --> SQLite DB (wacli.db) --> Python Backend (CLI) --> Action Items
```

### Components
| Component | Technology | Purpose |
|-----------|------------|---------|
| wacli | Go (whatsmeow) | WhatsApp authentication & message sync |
| database.py | Python | Query wacli SQLite DB |
| parser.py | Python | Rule-based message parsing |
| llm_client.py | Python | LLM-based parsing (LiteLLM) |
| cli.py | Python | CLI entry point |

## Configuration

### Environment Variables
- `LITELLM_BASE_URL`: LiteLLM endpoint (e.g., https://litellm.protium.co.in/v1)
- `LITELLM_API_KEY`: API key for LLM service

### config.yaml
```yaml
llm:
  model: "gemini/gemini-2.5-flash"
  confidence_threshold: 0.5
  batch_size: 10
database:
  path: "~/.wacli/wacli.db"
parser:
  use_llm: true
  fallback_to_rule_based: true
```

## Usage

### List Groups
```bash
python -m src.backend.cli --list
```

### Extract from Group (by Name)
```bash
python -m src.backend.cli "Group Name" --no-llm
```

### Extract from Group (by JID)
```bash
python -m src.backend.cli "120363425200420772@g.us" --use-llm
```

### Extract from All Groups
```bash
python -m src.backend.cli --all
```

## Features

1. **Hybrid Parsing**: Rule-based (fast, offline) + LLM-based (accurate)
2. **JID Support**: Use group JID for reliable identification
3. **Logging**: Python logging module for debugging
4. **Flexible Output**: Text output with priority, assignee, deadline

## Acceptance Criteria

- [x] Query wacli SQLite DB for messages
- [x] Support group name and JID lookup
 - [x] Rule-based parser extracts action items
- [x] LLM parser (gemini/gemini-2.5-flash) extracts action items
- [x] CLI supports --use-llm and --no-llm flags
- [x] Python logging used instead of print statements
- [x] Config loaded from config.yaml

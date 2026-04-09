# Sammurai - WhatsApp Group Analyzer with AI

Sammurai is an intelligent WhatsApp group analyzer that uses AI to help you stay on top of conversations. It extracts topics, identifies action items, summarizes shared documents, and generates daily digests—saving you time by automating the process of skimming through multiple group chats.

## Features

### Topic Extraction
Automatically identifies recurring themes and topics being discussed in your WhatsApp groups. From parking issues to school events, Sammurai groups related messages together and provides concise summaries.

### Action Items
Intelligent extraction of tasks and action items from group conversations. Each item includes:
- Task description
- Priority level (High/Medium/Low)
- Category (School, Bills, Community, Events, Work, Other)
- Assignee
- Deadline
- Related resources (URLs, documents)

### Document Summarization
When group members share documents, PDFs, or links, Sammurai can:
- Fetch and parse document content
- Generate concise summaries
- Extract key dates and important information

### Daily Digests
Get a formatted daily digest of all your WhatsApp groups with:
- Trending topics ranked by message count
- Document summaries
- Aggregated task lists grouped by category

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Sammurai                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │
│  │  WhatsApp DB │───▶│   Parser     │───▶│ LLM Client   │    │
│  │  (SQLite)    │    │              │    │  (LiteLLM)   │    │
│  └──────────────┘    └──────────────┘    └──────────────┘    │
│         │                                       │              │
│         │                                       ▼              │
│         │                              ┌──────────────┐       │
│         │                              │    Topic     │       │
│         │                              │  Extractor   │       │
│         │                              └──────────────┘       │
│         │                                       │              │
│         │                                       ▼              │
│         │                              ┌──────────────┐       │
│         └─────────────────────────────▶│ Daily Digest │       │
│                                        │   Output     │       │
│                                        └──────────────┘       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Component Overview:
- WhatsApp DB: SQLite database (wacli format) containing message data
- Parser: Parses raw message data into structured Message objects
- LLM Client: Interfaces with AI models via LiteLLM for NLP tasks
- Topic Extractor: Identifies topics, summaries, and action items
- Daily Digest: Formats results into readable markdown output
```

### Components

| Component | Description |
|-----------|-------------|
| **Database** | SQLite-based storage reading from wacli's message database |
| **Parser** | Converts raw database records into structured Message objects |
| **LLM Client** | Handles communication with AI models (OpenAI, Anthropic, Gemini, etc.) |
| **Topic Extractor** | Core AI logic for extracting topics, action items, and summaries |
| **Document Parser** | Extracts text from PDFs and other documents |
| **CLI** | Command-line interface for interacting with Sammurai |

## Quick Start Guide

### Prerequisites

- **Python 3.12+** - Required for modern Python features
- **SQLite3** - Bundled with Python
- **wacli** - WhatsApp CLI tool that stores message data

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/sammurai.git
   cd sammurai
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv ~/.venv/sammurai
   source ~/.venv/sammurai/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip3 install -r requirements.txt
   ```

4. **Download spaCy model** (for NLP processing)
   ```bash
   python -m spacy download en_core_web_sm
   ```

#### WhatsApp Sync Service

Sammurai reads from the local wacli database. To keep messages up to date automatically, run wacli as a systemd user service:

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

## Configuration

1. **Copy the example configuration**
   ```bash
   cp config.yaml.example config.yaml
   ```

2. **Set environment variables** for your LLM provider:
   ```bash
   export LITELLM_BASE_URL="https://your-litellm-url.com"
   export LITELLM_API_KEY="your-api-key"
   ```

3. **Edit `config.yaml`** to customize:
   - Model selection (default: `gemini-2.0-flash`)
   - Confidence threshold for results
   - Batch sizes for processing
   - Default groups to process
   - Parallel processing settings

### Basic Usage

```bash
# List all available WhatsApp groups
python -m src.backend.cli

# Extract action items from a specific group
python -m src.backend.cli "Family Group"

# Process all groups
python -m src.backend.cli --all

# Limit to last 7 days of messages
python -m src.backend.cli "Group Name" --days 7

# Process with specific batch size
python -m src.backend.cli "Group Name" --batch-size 20
```

## Configuration Reference

### config.yaml Structure

```yaml
llm:
  # Model to use (default: gemini-2.0-flash)
  model: "gemini-2.0-flash"
  
  # Confidence threshold (only include results above this)
  confidence_threshold: 0.75
  
  # Batch size for processing messages
  batch_size: 10

# Database
database:
  # Path to wacli database
  path: "~/.wacli/wacli.db"

# Parser
parser:
  # Use LLM parser (set to false to use rule-based only)
  use_llm: true
  
  # Fallback to rule-based if LLM fails
  fallback_to_rule_based: true

# Parallel processing
parallel:
  # List of group names to process by default
  groups:
    - "GROUP_ID_1"
    - "GROUP_ID_2"
   
  # Number of concurrent groups to process
  workers: 5
  
  # Number of concurrent batches within a group
  batch_workers: 3
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LITELLM_BASE_URL` | Base URL for LiteLLM proxy or custom endpoint |
| `LITELLM_API_KEY` | API key for your LLM provider |
| `OPENAI_API_KEY` | OpenAI API key (if using OpenAI directly) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |

## Development

### Running Tests

```bash
# Run all tests with coverage
pytest --cov=src/backend --cov-report=xml

# Run a specific test file
pytest tests/test_models.py

# Run a specific test function
pytest tests/test_models.py::test_actionable_item_validation

# Run tests matching a pattern
pytest -k "test_topic"

# Generate HTML coverage report
pytest --cov=src/backend --cov-report=term-missing --cov-report=html
```

### Code Quality

```bash
# Run linting with ruff
ruff check src/

# Format code
ruff format src/
```

## Security

Sammurai implements several security measures to protect your data:

- **HTTPS Enforcement**: All external URLs must use HTTPS protocol
- **SSRF Protection**: URLs are validated to prevent Server-Side Request Forgery attacks by checking resolved IPs against private/internal ranges
- **DNS Rebinding Prevention**: Document fetching uses resolved IPs with original hostnames in headers
- **PII Redaction**: Sensitive information is redacted from logs
- **Input Validation**: All inputs are validated using Pydantic models
- **Environment Variable Security**: API keys are stored in environment variables, not in configuration files

## License

MIT License - See LICENSE file for details

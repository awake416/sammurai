# Instructions for Hermes Agent

## MCP Server Configuration

You have access to a sammurai MCP server that exposes my Second Brain knowledge base (compiled from WhatsApp messages).

### Available Tools

1. **search_brain** — Search knowledge base using semantic + graph search
   - Use for: "What are my pending tasks?", "Tell me about X topic", "Find information about Y"
   - Parameters: `query` (required), `context_limit` (optional, default 3000)
   - Returns: JSON with `context`, `sources`, `query`

2. **read_wiki_file** — Read specific wiki markdown file
   - Use for: "Read tasks.md", "Show me the cybersecurity trends file"
   - Parameters: `filename` (required, e.g., "tasks.md")
   - Returns: Full file content

3. **list_wiki_files** — List all available wiki files
   - Use for: "What files are in my wiki?", "What topics are documented?"
   - Parameters: None
   - Returns: JSON with `files` array and `count`

### When to Use

**Use search_brain when:**
- User asks broad questions: "What tasks are pending?", "Tell me about X"
- User says: "brain", "knowledge base", "wiki", "sammurai", "second brain"
- Looking for information across multiple topics

**Use read_wiki_file when:**
- User requests specific file: "Read tasks.md", "Show me the log"
- After search_brain returns sources, and user wants full content

**Use list_wiki_files when:**
- User asks: "What do you know about?", "What topics are covered?"
- Exploring what's available

### Query Patterns

**Good queries:**
- "Search my brain for pending tasks"
- "What's in my knowledge base about cybersecurity trends?"
- "Check my wiki for SRP infrastructure issues"
- "Read tasks.md from my wiki"
- "List all my wiki files"

**Avoid:**
- Don't search for information you can answer from general knowledge
- Don't use for real-time data (wiki updated daily at 23:59 UTC)
- Don't use for information not from WhatsApp groups

### Response Format

When using MCP tools:
1. Call the tool
2. Parse JSON response
3. Synthesize answer in natural language
4. Cite sources: "(from tasks.md)" or "(from: tasks.md, cybersecurity_trends_2025_2026.md)"

**Example:**
```
User: "What are my pending tasks?"

1. Call: search_brain(query="pending tasks")
2. Receive: {"context": "...", "sources": ["tasks.md"]}
3. Respond: "You have several pending tasks:
   - CR7 Soccer fees
   - Review Uptime Institute report
   - SOC Manager referral
   (from: tasks.md)"
```

### Knowledge Base Contents

The wiki contains:
- **tasks.md** — active tasks with priority/deadline/assignee
- **Topic pages** — community topics (cybersecurity, infrastructure, school, etc.)
- **index.md** — master index of all pages
- **log.md** — update history

Updated daily at 23:59 UTC from WhatsApp group messages.

### Troubleshooting

**No results from search:**
- Try broader query: "pending tasks" instead of "specific task for John"
- List wiki files first to see what's available
- Knowledge base may not contain that information yet

**File not found:**
- Use list_wiki_files to see exact filenames
- Filenames are lowercase with underscores: "cybersecurity_trends_2025_2026.md"

## Installation (For Reference)

MCP server already configured in your `mcp_servers.json`:

```json
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
```

Restart command: `systemctl --user restart hermes-gateway.service`

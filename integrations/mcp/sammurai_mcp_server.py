#!$HOME/.venv/bin/python3
"""Sammurai MCP Server - exposes cognee knowledge base via MCP protocol.

Usage:
    python sammurai_mcp_server.py

Environment:
    SAMMURAI_WIKI_PATH: Path to wiki (default: ~/sammurai-brain)
    SAMMURAI_CONFIG: Path to config.yaml (default: ~/ai/sammurai/config.yaml)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add sammurai to path
SAMMURAI_ROOT = Path.home() / "ai" / "sammurai"
sys.path.insert(0, str(SAMMURAI_ROOT / "src"))

try:
    import yaml
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    from backend.cognee_store import CogneeStore
    from backend.entity_store import EntityStore
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}", file=sys.stderr)
    print("Install: pip install mcp pyyaml", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sammurai-mcp")

# Global stores
_store: CogneeStore | None = None
_entity_store: EntityStore | None = None


def get_store() -> CogneeStore:
    """Lazy-load cognee store."""
    global _store
    if _store is None:
        wiki_path = Path(
            os.getenv("SAMMURAI_WIKI_PATH", "~/sammurai-brain")
        ).expanduser()

        config_path = Path(
            os.getenv("SAMMURAI_CONFIG", SAMMURAI_ROOT / "config.yaml")
        ).expanduser()

        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)

        logger.info(f"Initializing cognee store: {wiki_path}")
        _store = CogneeStore(wiki_path=str(wiki_path), config=config)

    return _store


def get_entity_store() -> EntityStore:
    """Lazy-load entity store."""
    global _entity_store
    if _entity_store is None:
        wiki_path = Path(
            os.getenv("SAMMURAI_WIKI_PATH", "~/sammurai-brain")
        ).expanduser()
        db_path = wiki_path / "sammurai.db"
        logger.info(f"Initializing entity store: {db_path}")
        _entity_store = EntityStore(db_path=str(db_path))

    return _entity_store


# Create MCP server
app = Server("sammurai-cognee")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="search_brain",
            description="Search sammurai Second Brain (cognee RAG index). Returns relevant context from compiled wiki. Use for queries about tasks, topics, documents, or any information from WhatsApp groups.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language question to search for in the knowledge base",
                    },
                    "context_limit": {
                        "type": "integer",
                        "description": "Maximum characters of context to return (default: 3000)",
                        "default": 3000,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_entities",
            description="Search structured entities (people, activities, events, locations, organizations) extracted from WhatsApp messages. Use for specific queries like 'What is Bob's soccer schedule?' or 'When does CR7 Soccer meet?'. Much faster than search_brain (sub-second vs 15-19s).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (entity name, activity, person, etc.)",
                    },
                    "entity_type": {
                        "type": "string",
                        "description": "Optional filter: Person, Activity, Event, Location, Organization",
                        "enum": ["Person", "Activity", "Event", "Location", "Organization"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_entity_relations",
            description="Get all relations for a specific entity (e.g., 'Bob' -> ATTENDS -> 'CR7 Soccer' with schedule). Use after search_entities to get detailed info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Entity name (e.g., 'Bob', 'CR7 Soccer')",
                    },
                    "entity_type": {
                        "type": "string",
                        "description": "Optional entity type filter",
                        "enum": ["Person", "Activity", "Event", "Location", "Organization"],
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "Optional relation type filter",
                        "enum": ["ATTENDS", "PAYS", "SCHEDULED_FOR", "LOCATED_AT", "MEMBER_OF", "RELATED_TO"],
                    },
                },
                "required": ["entity_name"],
            },
        ),
        Tool(
            name="read_wiki_file",
            description="Read a specific wiki markdown file by name. Use when you know the exact filename (e.g., 'tasks.md', 'cybersecurity_trends_2025_2026.md').",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the wiki file to read (e.g., 'tasks.md')",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="list_wiki_files",
            description="List all available wiki markdown files. Use to discover what topics are documented.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle MCP tool calls."""
    try:
        if name == "search_brain":
            return await search_brain(arguments)
        elif name == "search_entities":
            return await search_entities(arguments)
        elif name == "get_entity_relations":
            return await get_entity_relations_tool(arguments)
        elif name == "read_wiki_file":
            return await read_wiki_file(arguments)
        elif name == "list_wiki_files":
            return await list_wiki_files(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {e}")]


async def search_brain(args: dict) -> list[TextContent]:
    """Search cognee knowledge base."""
    query = args.get("query", "").strip()
    context_limit = args.get("context_limit", 3000)

    if not query:
        return [TextContent(type="text", text="Error: query is required")]

    # Don't cache store - recreate each time to avoid lock
    global _store
    _store = None

    store = get_store()
    context = store.get_relevant_context(query, context_limit=context_limit)

    # Close cognee connections to release locks
    try:
        import cognee
        await cognee.prune.prune_data()  # Release connections
    except Exception:
        pass

    if not context:
        return [
            TextContent(
                type="text",
                text="No relevant information found in the knowledge base.",
            )
        ]

    # Extract sources (basic heuristic)
    wiki_path = Path(store.wiki_path) / "wiki"
    sources = []
    if wiki_path.exists():
        for wf in wiki_path.glob("*.md"):
            if wf.name in context or wf.stem in context:
                sources.append(wf.name)

    result = {
        "context": context,
        "sources": sources if sources else ["wiki"],
        "query": query,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def read_wiki_file(args: dict) -> list[TextContent]:
    """Read a specific wiki file."""
    filename = args.get("filename", "").strip()

    if not filename:
        return [TextContent(type="text", text="Error: filename is required")]

    store = get_store()
    wiki_path = Path(store.wiki_path) / "wiki"
    file_path = (wiki_path / filename).resolve()

    # Path traversal check
    if not str(file_path).startswith(str(wiki_path.resolve())):
        return [TextContent(type="text", text="Error: Invalid file path")]

    if not file_path.exists():
        return [TextContent(type="text", text=f"Error: File not found: {filename}")]

    try:
        content = file_path.read_text(encoding="utf-8")
        return [TextContent(type="text", text=content)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error reading file: {e}")]


async def search_entities(args: dict) -> list[TextContent]:
    """Search entity store (fast SQLite FTS5 lookup)."""
    query = args.get("query", "").strip()
    entity_type = args.get("entity_type")
    limit = args.get("limit", 20)

    if not query:
        return [TextContent(type="text", text="Error: query is required")]

    entity_store = get_entity_store()
    results = entity_store.search(query, entity_type=entity_type, limit=limit)

    if not results:
        return [
            TextContent(
                type="text",
                text=f"No entities found matching '{query}'",
            )
        ]

    # Format results with metadata
    formatted_results = []
    for entity in results:
        entity_str = f"**{entity['entity_name']}** ({entity['entity_type']})\n"

        # Format metadata
        metadata = entity.get("metadata", {})
        if metadata:
            entity_str += "Metadata:\n"
            for key, value in metadata.items():
                if value:
                    entity_str += f"  - {key}: {value}\n"

        # Show source context
        if entity.get("group_name"):
            entity_str += f"Source: {entity['group_name']}"
            if entity.get("message_timestamp"):
                entity_str += f" ({entity['message_timestamp'][:10]})"
            entity_str += "\n"

        formatted_results.append(entity_str)

    result = {
        "query": query,
        "count": len(results),
        "entities": formatted_results,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def get_entity_relations_tool(args: dict) -> list[TextContent]:
    """Get relations for an entity."""
    entity_name = args.get("entity_name", "").strip()
    entity_type = args.get("entity_type")
    relation_type = args.get("relation_type")

    if not entity_name:
        return [TextContent(type="text", text="Error: entity_name is required")]

    entity_store = get_entity_store()

    # Get entity details
    entity = entity_store.get_entity(entity_name, entity_type=entity_type)
    if not entity:
        return [
            TextContent(
                type="text",
                text=f"Entity not found: {entity_name}",
            )
        ]

    # Get relations
    relations = entity_store.get_relations(
        entity_name, entity_type=entity_type, relation_type=relation_type
    )

    result = {
        "entity": {
            "name": entity["entity_name"],
            "type": entity["entity_type"],
            "metadata": entity.get("metadata", {}),
        },
        "relations": [],
    }

    for rel in relations:
        # Determine direction (is entity source or target?)
        if rel["source_name"] == entity_name:
            direction = "→"
            other_name = rel["target_name"]
            other_type = rel["target_type"]
        else:
            direction = "←"
            other_name = rel["source_name"]
            other_type = rel["source_type"]

        result["relations"].append({
            "direction": direction,
            "relation_type": rel["relation_type"],
            "other_entity": f"{other_name} ({other_type})",
            "properties": rel.get("properties", {}),
        })

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def list_wiki_files(args: dict) -> list[TextContent]:
    """List all wiki files."""
    store = get_store()
    wiki_path = Path(store.wiki_path) / "wiki"

    if not wiki_path.exists():
        return [TextContent(type="text", text="Error: Wiki directory not found")]

    files = sorted([f.name for f in wiki_path.glob("*.md")])

    if not files:
        return [TextContent(type="text", text="No wiki files found")]

    result = {"files": files, "count": len(files)}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    """Run MCP server via stdio."""
    import os

    logger.info("Starting Sammurai MCP Server")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

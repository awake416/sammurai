# Entity Store Usage Guide

Sammurai's entity store provides fast, structured lookups for people, activities, events, locations, and organizations extracted from WhatsApp messages.

## Architecture

- **Storage**: SQLite + FTS5 at `~/sammurai-brain/sammurai.db`
- **Performance**: Sub-second queries (vs 15-19s for cognee graph)
- **Indexing**: Full-text search over entity name/type/metadata
- **Relations**: Source/target entities + properties (schedule, fee, contact)

## Extraction

Entities extracted automatically via LLM when using `--use-llm` flag:

```bash
# Extract from single group (last 30 days)
python -m src.backend.cli "CR7 SOCCER SCHOOL" --days 30 --use-llm

# Extract from all groups
python -m src.backend.cli --all --use-llm
```

### What Gets Extracted

**Entity Types:**
- **Person**: Names mentioned in context (e.g., "Bob has soccer class")
- **Activity**: Classes, sports, events (e.g., "CR7 Soccer", "Swimming lessons")
- **Event**: One-time occasions (e.g., "Annual Day", "Parent-teacher meeting")
- **Location**: Places, addresses (e.g., "SRP School", "Community Hall")
- **Organization**: Groups, committees (e.g., "RWA Committee", "School PTA")

**Metadata Fields:**
- `schedule`: Time/day if mentioned
- `fee`: Payment amounts
- `contact`: Phone/email/UPI
- `location`: Addresses
- `deadline`: Dates (YYYY-MM-DD)
- `notes`: Other details

**Relations:**
- `ATTENDS`: Person → Activity (with schedule/fee properties)
- `PAYS`: Person → Activity/Event (with payment details)
- `SCHEDULED_FOR`: Activity → Date/Time
- `LOCATED_AT`: Activity → Location
- `MEMBER_OF`: Person → Organization
- `RELATED_TO`: Generic connections (e.g., parent-child)

## Querying

### Python API

```python
from pathlib import Path
from src.backend.entity_store import EntityStore

# Initialize
db_path = Path.home() / "sammurai-brain" / "sammurai.db"
store = EntityStore(str(db_path))

# Full-text search
results = store.search("soccer", limit=10)
for r in results:
    print(f"{r['entity_name']} ({r['entity_type']})")
    print(f"  Metadata: {r['metadata']}")

# Search with type filter
activities = store.search("Activity", entity_type="Activity", limit=20)

# Get specific entity
entity = store.get_entity("Bob", "Person")
if entity:
    print(f"Metadata: {entity['metadata']}")

# Get relations
relations = store.get_relations("Bob", "Person")
for rel in relations:
    print(f"{rel['source_name']} -{rel['relation_type']}-> {rel['target_name']}")
    print(f"  Properties: {rel['properties']}")

# Get relations by type
attends = store.get_relations("Bob", "Person", relation_type="ATTENDS")

# Entity count
total = store.count_entities()
people_count = store.count_entities("Person")
```

### MCP Tools (Hermes Integration)

MCP server exposes entity store to Hermes agent:

**Tools:**
- `search_entities(query, entity_type?, limit?)` — FTS5 search
- `get_entity_relations(entity_name, entity_type?, relation_type?)` — Relation lookup

**Example Queries:**
- "What is Bob's soccer schedule?" → search_entities("Bob") → get_entity_relations("Bob", "Person", "ATTENDS")
- "When does CR7 Soccer meet?" → search_entities("CR7 Soccer")
- "Who attends gymnastics class?" → search_entities("gymnastics") → filter by ATTENDS relation

**Setup:**
```bash
# MCP server already configured in ~/.hermes/config.yaml
systemctl --user restart hermes-gateway.service

# Query via WhatsApp
"Search my brain for Bob's soccer info"
"What activities does Alice attend?"
```

## Example Scenarios

### Scenario 1: School Activity Tracking

**Message:** "Bob has CR7 Soccer class at 4:45 PM today. Pay ₹5000 to 5551234567."

**Entities Extracted:**
```python
{
  "entity_name": "Bob",
  "entity_type": "Person",
  "metadata": {"notes": "Parent: Charlie"},
  "relations": [{
    "relation_type": "ATTENDS",
    "target_entity_name": "CR7 Soccer",
    "target_entity_type": "Activity",
    "properties": {"schedule": "4:45 PM", "fee": "₹5000"}
  }]
}

{
  "entity_name": "CR7 Soccer",
  "entity_type": "Activity",
  "metadata": {
    "schedule": "4:45 PM",
    "contact": "5551234567",
    "location": "Sobha Royal Pavilion"
  }
}
```

**Query:**
```python
# Get Bob's schedule
entity = store.get_entity("Bob", "Person")
relations = store.get_relations("Bob", "Person", "ATTENDS")
# Returns: CR7 Soccer @ 4:45 PM, fee ₹5000
```

### Scenario 2: Fee Payment Tracking

**Message:** "May 2026 fee pending: Dave, Eve, Alice. Pay to 5551234567."

**Entities Extracted:**
- Dave (Person) - metadata: {fee: "May 2026 pending"}
- Eve (Person) - metadata: {fee: "May 2026 pending"}
- Alice (Person) - metadata: {fee: "May 2026 pending"}

**Query:**
```python
# Find all pending fees
results = store.search("pending")
for r in results:
    if r['metadata'].get('fee'):
        print(f"{r['entity_name']}: {r['metadata']['fee']}")
```

### Scenario 3: Contact Lookup

**Message:** "For Kannada tuition, contact Ashwini at 5559876543."

**Query:**
```python
# Find contact for Kannada tuition
results = store.search("Kannada tuition")
if results:
    contact = results[0]['metadata'].get('contact')
    # Returns: "5559876543 (Ashwini)"
```

## Performance Metrics

From CR7 Soccer group extraction (120 messages, 30 days):
- Extraction time: ~100s (6 batches, parallel)
- Entities extracted: 81
- Relations created: 89
- Storage time: <2s
- Query time: <100ms (FTS5)

Comparison with cognee:
- Entity search: <1s (SQLite FTS5) vs 15-19s (cognee graph)
- No locks: file-based SQLite vs ladybug multi-process failures
- Structured queries: exact entity match vs fuzzy semantic search

## Troubleshooting

### Entity Merge Issues

Entities with same name + type auto-merge metadata:
```python
# First extraction
store.add_entity("CR7 Soccer", "Activity", {"schedule": "4:45 PM"})

# Second extraction (same entity, new metadata)
store.add_entity("CR7 Soccer", "Activity", {"fee": "₹5000", "contact": "5551234567"})

# Result: merged metadata
# {"schedule": "4:45 PM", "fee": "₹5000", "contact": "5551234567"}
```

### Relation Failures

Relations require both source and target entities to exist:
```python
# This fails (target not found)
store.add_relation("Bob", "Person", "ATTENDS", "Unknown Activity", "Activity")
# Returns: None
# Logs: "Target entity not found: Unknown Activity (Activity)"
```

**Solution:** Extract messages in larger batches (more context) or manually add missing entities.

### FTS5 Search Tips

- FTS5 matches exact tokens: "soccer" matches "soccer", "CR7 Soccer", "Soccer School"
- Use wildcards for partial match: `search("soc*")` matches "soccer", "social"
- Phrase search: `search('"CR7 Soccer School"')` (exact phrase)
- Boolean operators: `search("soccer AND fee")`, `search("soccer OR gymnastics")`

## Schema

### Entity Table

```sql
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    entity_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,  -- Person|Activity|Event|Location|Organization|Other
    metadata_json TEXT NOT NULL,
    group_jid TEXT,
    group_name TEXT,
    message_timestamp TEXT,
    message_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### Relation Table

```sql
CREATE TABLE entity_relations (
    id INTEGER PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,  -- ATTENDS|PAYS|SCHEDULED_FOR|LOCATED_AT|MEMBER_OF|RELATED_TO
    target_entity_id INTEGER NOT NULL,
    properties_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_entity_id) REFERENCES entities(id),
    FOREIGN KEY (target_entity_id) REFERENCES entities(id)
);
```

### FTS5 Index

```sql
CREATE VIRTUAL TABLE entities_fts USING fts5(
    entity_name,
    entity_type,
    metadata_json
);
```

## Observability

Extraction failures logged automatically:

```
INFO - Extracted 12 action items, 81 entities
INFO - Stored 81 entities, 89 relations in entity store
WARNING - Entity extraction failures: 3/84 (3.6% failure rate)
WARNING - Failed to create 2 relations (missing target entities)
```

Check entity counts:
```bash
python -c "
from pathlib import Path
from src.backend.entity_store import EntityStore
store = EntityStore(str(Path.home() / 'sammurai-brain' / 'sammurai.db'))
print(f'Total: {store.count_entities()}')
for t in store.ENTITY_TYPES:
    print(f'  {t}: {store.count_entities(t)}')
"
```

## Next Steps

1. **Schedule daily extraction**: Run `sammurai-digest.timer` to auto-populate entity store
2. **Test Hermes queries**: Ask via WhatsApp after fixing bridge
3. **Entity deduplication**: Review merged entities for accuracy
4. **Extend metadata**: Add custom fields per entity type (e.g., `grade` for Person)
5. **Relation validation**: Audit ATTENDS relations for schedule consistency

## References

- Entity store implementation: `src/backend/entity_store.py`
- LLM extraction schema: `src/backend/llm_client.py` (lines 515-550)
- MCP tools: `integrations/mcp/sammurai_mcp_server.py`
- Tests: `tests/test_entity_extraction.py`

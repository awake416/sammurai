#!/usr/bin/env python3
"""Test MCP entity search tools directly."""

import sys
import os
from pathlib import Path

# Add sammurai to path
sys.path.insert(0, str(Path.home() / "ai" / "sammurai" / "src"))

# Load env vars
env_file = Path.home() / ".config" / "sammurai" / "env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key] = val

from backend.entity_store import EntityStore

# Initialize
wiki_path = Path.home() / "sammurai-brain"
db_path = wiki_path / "sammurai.db"
store = EntityStore(db_path=str(db_path))

print("=" * 60)
print("MCP TOOL TEST: search_entities")
print("=" * 60)

# Test 1: Search for soccer
print("\n[TEST 1] Search 'soccer':")
results = store.search("soccer", limit=5)
print(f"Found {len(results)} results")
for r in results[:3]:
    print(f"  - {r['entity_name']} ({r['entity_type']})")
    if r.get('metadata', {}).get('schedule'):
        print(f"    Schedule: {r['metadata']['schedule']}")
    if r.get('metadata', {}).get('fee'):
        print(f"    Fee: {r['metadata']['fee']}")

# Test 2: Search for people
print("\n[TEST 2] Search 'Person' type:")
results = store.search("Activity", entity_type="Person", limit=5)
print(f"Found {len(results)} results")
for r in results[:3]:
    print(f"  - {r['entity_name']} ({r['entity_type']})")

# Test 3: Get entity relations
print("\n[TEST 3] get_entity_relations for 'Alice':")
relations = store.get_relations("Alice", "Person")
print(f"Found {len(relations)} relations")
for rel in relations:
    if rel['source_name'] == 'Alice':
        print(f"  Alice -{rel['relation_type']}-> {rel['target_name']}")
    else:
        print(f"  {rel['source_name']} -{rel['relation_type']}-> Alice")
    if rel.get('properties'):
        for k, v in rel['properties'].items():
            if v:
                print(f"    {k}: {v}")

# Test 4: Specific entity query (simulating Hermes query)
print("\n[TEST 4] Query: 'What is Alice's soccer schedule?'")
# Search for Alice
anayra = store.get_entity("Alice", "Person")
if anayra:
    print(f"Entity: {anayra['entity_name']}")
    print(f"Metadata: {anayra.get('metadata', {})}")

    # Get relations
    relations = store.get_relations("Alice", "Person", relation_type="ATTENDS")
    print(f"\nAttends:")
    for rel in relations:
        target_name = rel['target_name'] if rel['source_name'] == 'Alice' else rel['source_name']
        print(f"  - {target_name}")
        if rel.get('properties'):
            schedule = rel['properties'].get('schedule')
            fee = rel['properties'].get('fee')
            if schedule:
                print(f"    Schedule: {schedule}")
            if fee:
                print(f"    Fee: {fee}")

# Test 5: Activity search
print("\n[TEST 5] Query: 'When does CR7 Soccer meet?'")
results = store.search("CR7 Soccer", limit=10)
for r in results:
    if 'CR7' in r['entity_name'] or 'Soccer' in r['entity_name']:
        print(f"\nEntity: {r['entity_name']} ({r['entity_type']})")
        meta = r.get('metadata', {})
        if meta.get('schedule'):
            print(f"  Schedule: {meta['schedule']}")
        if meta.get('location'):
            print(f"  Location: {meta['location']}")
        if meta.get('contact'):
            print(f"  Contact: {meta['contact']}")

print("\n" + "=" * 60)
print("Entity count:", store.count_entities())
print("=" * 60)

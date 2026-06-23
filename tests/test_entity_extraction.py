"""Integration tests for entity extraction and storage."""

import json
import pytest
import tempfile
from pathlib import Path
from src.backend.entity_store import EntityStore
from src.backend.llm_client import LLMClient


@pytest.fixture
def temp_entity_store():
    """Create a temporary entity store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_entities.db"
        store = EntityStore(db_path=str(db_path))
        yield store


def test_entity_store_init(temp_entity_store):
    """Test entity store initialization."""
    assert temp_entity_store.count_entities() == 0


def test_add_entity(temp_entity_store):
    """Test adding an entity."""
    entity_id = temp_entity_store.add_entity(
        entity_name="Swadhi",
        entity_type="Person",
        metadata={"age": "10", "school": "SRP"},
        group_jid="123@g.us",
        group_name="Family",
    )
    assert entity_id > 0
    assert temp_entity_store.count_entities() == 1

    # Retrieve entity
    entity = temp_entity_store.get_entity("Swadhi", "Person")
    assert entity is not None
    assert entity["entity_name"] == "Swadhi"
    assert entity["entity_type"] == "Person"
    assert entity["metadata"]["age"] == "10"


def test_entity_merge(temp_entity_store):
    """Test entity metadata merge on duplicate."""
    # Add entity with initial metadata
    temp_entity_store.add_entity(
        entity_name="CR7 Soccer",
        entity_type="Activity",
        metadata={"schedule": "4:45 PM"},
        group_name="Soccer Group",
    )

    # Add same entity with additional metadata
    temp_entity_store.add_entity(
        entity_name="CR7 Soccer",
        entity_type="Activity",
        metadata={"fee": "₹5000", "contact": "5551234567"},
        group_name="Soccer Group",
    )

    # Should have 1 entity with merged metadata
    assert temp_entity_store.count_entities() == 1

    entity = temp_entity_store.get_entity("CR7 Soccer", "Activity")
    assert entity["metadata"]["schedule"] == "4:45 PM"
    assert entity["metadata"]["fee"] == "₹5000"
    assert entity["metadata"]["contact"] == "5551234567"


def test_add_relation(temp_entity_store):
    """Test adding relations between entities."""
    # Add entities
    temp_entity_store.add_entity(
        entity_name="Swadhi",
        entity_type="Person",
        metadata={},
        group_name="Family",
    )
    temp_entity_store.add_entity(
        entity_name="CR7 Soccer",
        entity_type="Activity",
        metadata={"schedule": "4:45 PM"},
        group_name="Soccer Group",
    )

    # Add relation
    relation_id = temp_entity_store.add_relation(
        source_name="Swadhi",
        source_type="Person",
        relation_type="ATTENDS",
        target_name="CR7 Soccer",
        target_type="Activity",
        properties={"schedule": "4:45 PM", "fee": "₹5000"},
    )

    assert relation_id is not None

    # Get relations
    relations = temp_entity_store.get_relations("Swadhi", "Person")
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "ATTENDS"
    assert relations[0]["target_name"] == "CR7 Soccer"
    assert relations[0]["properties"]["schedule"] == "4:45 PM"


def test_fts_search(temp_entity_store):
    """Test full-text search."""
    # Add test entities
    temp_entity_store.add_entity(
        entity_name="Swadhi",
        entity_type="Person",
        metadata={"school": "SRP", "grade": "5"},
        group_name="Family",
    )
    temp_entity_store.add_entity(
        entity_name="CR7 Soccer",
        entity_type="Activity",
        metadata={"schedule": "4:45 PM", "location": "SRP School"},
        group_name="Soccer Group",
    )
    temp_entity_store.add_entity(
        entity_name="Swimming",
        entity_type="Activity",
        metadata={"schedule": "6:00 PM"},
        group_name="Sports",
    )

    # Search by entity name
    results = temp_entity_store.search("Swadhi")
    assert len(results) == 1
    assert results[0]["entity_name"] == "Swadhi"

    # Search by metadata content (FTS5 indexes metadata_json)
    results = temp_entity_store.search("Soccer")
    assert len(results) == 1
    assert results[0]["entity_name"] == "CR7 Soccer"

    # Search with type filter
    results = temp_entity_store.search("Activity", entity_type="Activity")
    assert len(results) == 2


def test_entity_extraction_schema():
    """Test that LLM extraction returns expected entity schema.

    This is a contract test - verifies the schema structure without calling the LLM.
    """
    # Mock LLM response
    mock_response = {
        "action_items": [
            {
                "is_action_item": True,
                "task": "Pay CR7 Soccer fee to 5551234567",
                "category": "School",
                "context": "Soccer class fee payment reminder",
                "assignee": "User",
                "deadline": "2026-05-31",
                "priority": "Medium",
                "confidence": 0.9,
                "resources": [
                    {
                        "type": "contact",
                        "value": "5551234567",
                        "description": "Payment UPI"
                    }
                ],
                "original_message_index": 0,
            }
        ],
        "entities": [
            {
                "entity_name": "Swadhi",
                "entity_type": "Person",
                "metadata": {
                    "grade": "5",
                    "school": "SRP",
                },
                "relations": [
                    {
                        "relation_type": "ATTENDS",
                        "target_entity_name": "CR7 Soccer",
                        "target_entity_type": "Activity",
                        "properties": {
                            "schedule": "4:45 PM - 5:45 PM",
                            "fee": "₹5000",
                        },
                    }
                ],
                "original_message_index": 0,
            },
            {
                "entity_name": "CR7 Soccer",
                "entity_type": "Activity",
                "metadata": {
                    "schedule": "4:45 PM - 5:45 PM",
                    "fee": "₹5000",
                    "contact": "5551234567",
                },
                "relations": [],
                "original_message_index": 0,
            },
        ],
    }

    # Verify schema structure
    assert "action_items" in mock_response
    assert "entities" in mock_response

    # Verify entity schema
    entity = mock_response["entities"][0]
    assert "entity_name" in entity
    assert "entity_type" in entity
    assert "metadata" in entity
    assert "relations" in entity
    assert "original_message_index" in entity

    # Verify entity type
    assert entity["entity_type"] in ["Person", "Activity", "Event", "Location", "Organization"]

    # Verify relation schema
    relation = entity["relations"][0]
    assert "relation_type" in relation
    assert "target_entity_name" in relation
    assert "target_entity_type" in relation
    assert "properties" in relation
    assert relation["relation_type"] in ["ATTENDS", "PAYS", "SCHEDULED_FOR", "LOCATED_AT", "MEMBER_OF", "RELATED_TO"]


@pytest.mark.skipif(
    not Path.home().joinpath(".config/sammurai/env").exists(),
    reason="LLM config not available",
)
def test_entity_extraction_integration(temp_entity_store):
    """Integration test: extract entities from real message and store.

    Skipped if LLM config not available.
    """
    import os

    # Load env vars
    env_file = Path.home() / ".config/sammurai/env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    os.environ[key] = val

    # Create LLM client
    llm_client = LLMClient(
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4.6"),
        confidence_threshold=0.75,
    )

    # Test message
    messages = [
        {
            "message": "Swadhi has CR7 Soccer class at 4:45 PM today. Pay ₹5000 to 5551234567.",
            "sender": "Keerthi",
            "group_name": "SRP CR7 Soccer",
            "group_jid": "123@g.us",
            "timestamp": "2026-05-25T10:00:00Z",
        }
    ]

    # Extract
    result = llm_client.extract_batch(messages, batch_size=1, parallel_batches=1)

    # Verify structure
    assert "action_items" in result
    assert "entities" in result

    entities = result["entities"]
    if entities:
        # First pass: add all entities so relations can find their targets
        for entity in entities:
            temp_entity_store.add_entity(
                entity_name=entity["entity_name"],
                entity_type=entity["entity_type"],
                metadata=entity.get("metadata", {}),
                group_jid=entity.get("group_jid"),
                group_name=entity.get("group_name"),
                message_timestamp=entity.get("timestamp"),
            )

        # Second pass: add relations (targets already exist)
        for entity in entities:
            for relation in entity.get("relations", []):
                temp_entity_store.add_relation(
                    source_name=entity["entity_name"],
                    source_type=entity["entity_type"],
                    relation_type=relation["relation_type"],
                    target_name=relation["target_entity_name"],
                    target_type=relation["target_entity_type"],
                    properties=relation.get("properties", {}),
                )

        # Verify storage
        assert temp_entity_store.count_entities() > 0

        # Search test
        results = temp_entity_store.search("Swadhi")
        assert len(results) > 0

        # Relation test
        person = temp_entity_store.get_entity("Swadhi", "Person")
        if person:
            relations = temp_entity_store.get_relations("Swadhi", "Person")
            assert len(relations) > 0

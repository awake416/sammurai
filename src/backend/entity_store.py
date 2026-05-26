"""Entity store: SQLite + FTS5 for structured entity storage and fast lookup.

Stores entities (Person, Activity, Event, Location, Organization) extracted from
WhatsApp messages with full-text search and relation tracking.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class EntityStore:
    """SQLite-backed entity storage with FTS5 search and relations."""

    ENTITY_TYPES = ["Person", "Activity", "Event", "Location", "Organization", "Other"]
    RELATION_TYPES = ["ATTENDS", "PAYS", "SCHEDULED_FOR", "LOCATED_AT", "MEMBER_OF", "RELATED_TO"]

    def __init__(self, db_path: str):
        """Initialize entity store.

        Args:
            db_path: Path to SQLite database file (e.g., ~/sammurai-brain/sammurai.db)
        """
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables and indexes if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    group_jid TEXT,
                    group_name TEXT,
                    message_timestamp TEXT,
                    message_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(entity_name);
                CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type);
                CREATE INDEX IF NOT EXISTS idx_group_jid ON entities(group_jid);

                CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                    entity_name,
                    entity_type,
                    metadata_json
                );

                CREATE TABLE IF NOT EXISTS entity_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_entity_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    target_entity_id INTEGER NOT NULL,
                    properties_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_entity_id) REFERENCES entities(id),
                    FOREIGN KEY (target_entity_id) REFERENCES entities(id)
                );

                CREATE INDEX IF NOT EXISTS idx_relation_source ON entity_relations(source_entity_id);
                CREATE INDEX IF NOT EXISTS idx_relation_target ON entity_relations(target_entity_id);
                CREATE INDEX IF NOT EXISTS idx_relation_type ON entity_relations(relation_type);
            """)
            conn.commit()
            logger.info(f"Initialized entity store: {self.db_path}")

    def add_entity(
        self,
        entity_name: str,
        entity_type: str,
        metadata: Dict[str, Any],
        group_jid: Optional[str] = None,
        group_name: Optional[str] = None,
        message_timestamp: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> int:
        """Add or update an entity.

        Args:
            entity_name: Name of the entity (e.g., "Bob", "CR7 Soccer")
            entity_type: Type from ENTITY_TYPES
            metadata: Dict of entity attributes (schedule, fee, contact, etc.)
            group_jid: WhatsApp group JID
            group_name: WhatsApp group name
            message_timestamp: ISO timestamp of source message
            message_id: Message ID for tracking

        Returns:
            Entity ID
        """
        if entity_type not in self.ENTITY_TYPES:
            logger.warning(f"Unknown entity type: {entity_type}, defaulting to Other")
            entity_type = "Other"

        metadata_json = json.dumps(metadata, ensure_ascii=False)

        with sqlite3.connect(self.db_path) as conn:
            # Check for existing entity (name + type match)
            cursor = conn.execute(
                "SELECT id, metadata_json FROM entities WHERE entity_name = ? AND entity_type = ?",
                (entity_name, entity_type),
            )
            existing = cursor.fetchone()

            if existing:
                # Merge metadata (new keys override)
                entity_id, old_metadata_json = existing
                old_metadata = json.loads(old_metadata_json)
                old_metadata.update(metadata)
                merged_json = json.dumps(old_metadata, ensure_ascii=False)

                # Update entity
                conn.execute(
                    """UPDATE entities
                       SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (merged_json, entity_id),
                )
                # Update FTS5 index manually
                conn.execute(
                    "DELETE FROM entities_fts WHERE rowid = ?",
                    (entity_id,),
                )
                conn.execute(
                    """INSERT INTO entities_fts(rowid, entity_name, entity_type, metadata_json)
                       VALUES (?, ?, ?, ?)""",
                    (entity_id, entity_name, entity_type, merged_json),
                )
                conn.commit()
                logger.debug(f"Updated entity: {entity_name} (id={entity_id})")
                return entity_id
            else:
                # Insert new entity
                cursor = conn.execute(
                    """INSERT INTO entities
                       (entity_name, entity_type, metadata_json, group_jid, group_name,
                        message_timestamp, message_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (entity_name, entity_type, metadata_json, group_jid, group_name,
                     message_timestamp, message_id),
                )
                entity_id = cursor.lastrowid
                # Insert into FTS5 index
                conn.execute(
                    """INSERT INTO entities_fts(rowid, entity_name, entity_type, metadata_json)
                       VALUES (?, ?, ?, ?)""",
                    (entity_id, entity_name, entity_type, metadata_json),
                )
                conn.commit()
                logger.debug(f"Added entity: {entity_name} (id={entity_id})")
                return entity_id

    def add_relation(
        self,
        source_name: str,
        source_type: str,
        relation_type: str,
        target_name: str,
        target_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Add a relation between two entities.

        Args:
            source_name: Source entity name
            source_type: Source entity type
            relation_type: Relation type from RELATION_TYPES
            target_name: Target entity name
            target_type: Target entity type
            properties: Optional relation properties (schedule, fee, etc.)

        Returns:
            Relation ID or None if entities not found
        """
        if relation_type not in self.RELATION_TYPES:
            logger.warning(f"Unknown relation type: {relation_type}, defaulting to RELATED_TO")
            relation_type = "RELATED_TO"

        with sqlite3.connect(self.db_path) as conn:
            # Get entity IDs
            cursor = conn.execute(
                "SELECT id FROM entities WHERE entity_name = ? AND entity_type = ?",
                (source_name, source_type),
            )
            source_row = cursor.fetchone()
            if not source_row:
                logger.warning(f"Source entity not found: {source_name} ({source_type})")
                return None

            cursor = conn.execute(
                "SELECT id FROM entities WHERE entity_name = ? AND entity_type = ?",
                (target_name, target_type),
            )
            target_row = cursor.fetchone()
            if not target_row:
                logger.warning(f"Target entity not found: {target_name} ({target_type})")
                return None

            source_id = source_row[0]
            target_id = target_row[0]
            properties_json = json.dumps(properties or {}, ensure_ascii=False)

            # Check for existing relation
            cursor = conn.execute(
                """SELECT id FROM entity_relations
                   WHERE source_entity_id = ? AND relation_type = ? AND target_entity_id = ?""",
                (source_id, relation_type, target_id),
            )
            existing = cursor.fetchone()

            if existing:
                # Update properties
                relation_id = existing[0]
                conn.execute(
                    "UPDATE entity_relations SET properties_json = ? WHERE id = ?",
                    (properties_json, relation_id),
                )
                conn.commit()
                logger.debug(f"Updated relation: {source_name} -{relation_type}-> {target_name}")
                return relation_id
            else:
                # Insert new relation
                cursor = conn.execute(
                    """INSERT INTO entity_relations
                       (source_entity_id, relation_type, target_entity_id, properties_json)
                       VALUES (?, ?, ?, ?)""",
                    (source_id, relation_type, target_id, properties_json),
                )
                conn.commit()
                relation_id = cursor.lastrowid
                logger.debug(f"Added relation: {source_name} -{relation_type}-> {target_name}")
                return relation_id

    def search(self, query: str, entity_type: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text search over entities.

        Args:
            query: Search query (FTS5 syntax supported)
            entity_type: Optional filter by entity type
            limit: Max results

        Returns:
            List of entity dicts with metadata
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if entity_type:
                cursor = conn.execute(
                    """SELECT e.*
                       FROM entities_fts f
                       JOIN entities e ON f.rowid = e.id
                       WHERE entities_fts MATCH ? AND e.entity_type = ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, entity_type, limit),
                )
            else:
                cursor = conn.execute(
                    """SELECT e.*
                       FROM entities_fts f
                       JOIN entities e ON f.rowid = e.id
                       WHERE entities_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, limit),
                )

            results = []
            for row in cursor.fetchall():
                entity = dict(row)
                entity["metadata"] = json.loads(entity["metadata_json"])
                del entity["metadata_json"]
                results.append(entity)

            return results

    def get_entity(self, entity_name: str, entity_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get entity by exact name match.

        Args:
            entity_name: Exact entity name
            entity_type: Optional type filter

        Returns:
            Entity dict or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if entity_type:
                cursor = conn.execute(
                    "SELECT * FROM entities WHERE entity_name = ? AND entity_type = ?",
                    (entity_name, entity_type),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM entities WHERE entity_name = ?",
                    (entity_name,),
                )

            row = cursor.fetchone()
            if not row:
                return None

            entity = dict(row)
            entity["metadata"] = json.loads(entity["metadata_json"])
            del entity["metadata_json"]
            return entity

    def get_relations(
        self,
        entity_name: str,
        entity_type: Optional[str] = None,
        relation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all relations for an entity.

        Args:
            entity_name: Entity name
            entity_type: Optional type filter
            relation_type: Optional relation type filter

        Returns:
            List of relation dicts with source/target entities
        """
        entity = self.get_entity(entity_name, entity_type)
        if not entity:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if relation_type:
                cursor = conn.execute(
                    """SELECT r.*,
                              s.entity_name as source_name, s.entity_type as source_type,
                              t.entity_name as target_name, t.entity_type as target_type
                       FROM entity_relations r
                       JOIN entities s ON r.source_entity_id = s.id
                       JOIN entities t ON r.target_entity_id = t.id
                       WHERE (r.source_entity_id = ? OR r.target_entity_id = ?)
                         AND r.relation_type = ?""",
                    (entity["id"], entity["id"], relation_type),
                )
            else:
                cursor = conn.execute(
                    """SELECT r.*,
                              s.entity_name as source_name, s.entity_type as source_type,
                              t.entity_name as target_name, t.entity_type as target_type
                       FROM entity_relations r
                       JOIN entities s ON r.source_entity_id = s.id
                       JOIN entities t ON r.target_entity_id = t.id
                       WHERE r.source_entity_id = ? OR r.target_entity_id = ?""",
                    (entity["id"], entity["id"]),
                )

            results = []
            for row in cursor.fetchall():
                relation = dict(row)
                relation["properties"] = json.loads(relation["properties_json"] or "{}")
                del relation["properties_json"]
                results.append(relation)

            return results

    def count_entities(self, entity_type: Optional[str] = None) -> int:
        """Count entities, optionally filtered by type."""
        with sqlite3.connect(self.db_path) as conn:
            if entity_type:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM entities WHERE entity_type = ?",
                    (entity_type,),
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM entities")
            return cursor.fetchone()[0]

# LLM Functionality Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enhance the WhatsApp Action Item Extractor by extracting related resources (URLs/docs), improving rule-based parsing with NLP, and adding group activity filtering.

**Architecture:** 
1. Update Pydantic models to support a separate `Resource` model linked to `ActionableItem`.
2. Update the LLM prompt in `llm_client.py` to extract these resources.
3. Replace regex-based parsing in `parser.py` with `spaCy` for better entity and intent recognition.
4. Update `database.py` and `cli.py` to support filtering groups by days active and showing last message time.

**Tech Stack:** Python, Pydantic, LiteLLM, spaCy, SQLite

---

### Task 1: Update Data Models for Resources

**Files:**
- Modify: `src/backend/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write the failing test**
```python
# tests/test_models.py
def test_actionable_item_with_resources():
    from src.backend.models import Resource, ResourceType
    
    resource = Resource(
        type=ResourceType.URL,
        value="https://example.com",
        description="Project spec"
    )
    
    item = ActionableItem(
        task="Review spec",
        resources=[resource]
    )
    
    assert len(item.resources) == 1
    assert item.resources[0].type == ResourceType.URL
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_models.py::test_actionable_item_with_resources -v`
Expected: FAIL with "ImportError: cannot import name 'Resource'"

**Step 3: Write minimal implementation**
```python
# src/backend/models.py
# Add to imports:
from enum import Enum

class ResourceType(str, Enum):
    URL = "url"
    DOCUMENT = "document"

class Resource(BaseModel):
    type: ResourceType = Field(..., description="Type of resource (url or document)")
    value: str = Field(..., description="The URL or document name")
    description: Optional[str] = Field(default=None, description="Brief description of the resource")

# Add to ActionableItem:
    resources: list[Resource] = Field(default_factory=list, description="Related URLs or documents")
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_models.py::test_actionable_item_with_resources -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/backend/models.py tests/test_models.py
git commit -m "feat(models): add Resource model for URLs and documents"
```

---

### Task 2: Update LLM Prompt for Resource Extraction

**Files:**
- Modify: `src/backend/llm_client.py`

**Step 1: Write the failing test**
```python
# tests/test_llm_client.py
from unittest.mock import patch, MagicMock
from src.backend.llm_client import LLMClient

@patch('src.backend.llm_client.requests.post')
def test_extract_resources(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '''
        {
            "is_action_item": true,
            "task": "Review doc",
            "assignee": "John",
            "deadline": null,
            "priority": "Medium",
            "confidence": 0.9,
            "resources": [
                {"type": "url", "value": "https://example.com", "description": "Spec"}
            ]
        }
        '''}}]
    }
    mock_post.return_value = mock_response
    
    client = LLMClient(base_url="http://test", api_key="test")
    result = client.extract_action_item("John please review https://example.com")
    
    assert "resources" in result
    assert len(result["resources"]) == 1
    assert result["resources"][0]["type"] == "url"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_llm_client.py::test_extract_resources -v`
Expected: FAIL (if test file doesn't exist, create it first)

**Step 3: Write minimal implementation**
```python
# src/backend/llm_client.py
# Update system_prompt in extract_action_item:
        system_prompt = """You are an expert at analyzing WhatsApp messages to extract action items/tasks.

Given a WhatsApp message, determine if it contains an actionable task that someone needs to do.

If it's an action item, respond with JSON in this exact format:
{
    "is_action_item": true,
    "task": "Brief description of what needs to be done",
    "assignee": "Person responsible (or 'unassigned' if unclear)",
    "deadline": "Deadline if mentioned (YYYY-MM-DD format, or null if not specified)",
    "priority": "High", "Medium", or "Low",
    "confidence": 0.0-1.0 (how confident you are this is an action item),
    "resources": [
        {
            "type": "url" or "document",
            "value": "The actual URL or document name mentioned",
            "description": "Brief description of what the resource is"
        }
    ]
}

If it's NOT an action item, respond with:
{
    "is_action_item": false,
    "confidence": 0.0-1.0
}

Rules:
- Only mark as action item if there's a clear task (verb + action)
- Ignore casual conversation, greetings, FYIs
- Extract specific assignee if mentioned (name or @mention)
- Extract deadline if explicitly stated
- Default priority to Medium unless urgent keywords present
- Extract any URLs (http/https) or document names (.pdf, .doc, etc) mentioned in relation to the task into the resources array. If none, return an empty array []."""

# Update extract_batch to include resources:
            if result and result.get("is_action_item") and result.get("confidence", 0) > 0.5:
                action_items.append({
                    "task": result.get("task"),
                    "assignee": result.get("assignee", "unassigned"),
                    "deadline": result.get("deadline"),
                    "priority": result.get("priority", "Medium"),
                    "confidence": result.get("confidence"),
                    "resources": result.get("resources", []),
                    "original_message": msg.get("message"),
                    "sender": msg.get("sender"),
                    "timestamp": msg.get("timestamp")
                })
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_llm_client.py::test_extract_resources -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/backend/llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): update prompt to extract related resources"
```

---

### Task 3: Add Group Filtering to Database

**Files:**
- Modify: `src/backend/database.py`
- Modify: `tests/test_database.py`

**Step 1: Write the failing test**
```python
# tests/test_database.py
from unittest.mock import patch, MagicMock
from src.backend.database import WhatsAppDB

@patch('sqlite3.connect')
def test_get_groups_with_activity(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    
    mock_cursor.fetchall.return_value = [
        {"jid": "1@g.us", "name": "Group 1", "last_activity": "2026-03-07 10:00:00"}
    ]
    
    db = WhatsAppDB("dummy.db")
    groups = db.get_groups(days_active=30)
    
    assert len(groups) == 1
    assert "last_activity" in groups[0]
    
    # Verify query contains date filtering
    call_args = mock_cursor.execute.call_args[0][0]
    assert "ts >=" in call_args or "timestamp >=" in call_args
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_database.py::test_get_groups_with_activity -v`
Expected: FAIL

**Step 3: Write minimal implementation**
```python
# src/backend/database.py
# Update get_groups:
    def get_groups(self, days_active: Optional[int] = None) -> list[dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT c.jid, c.name, MAX(m.ts) as last_activity
            FROM chats c
            LEFT JOIN messages m ON c.jid = m.chat_jid
            WHERE c.jid LIKE '%@g.us'
        """
        params = []
        
        if days_active is not None:
            # SQLite datetime logic for days ago
            query += " AND m.ts >= datetime('now', ?)"
            params.append(f'-{days_active} days')
            
        query += " GROUP BY c.jid, c.name ORDER BY last_activity DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "jid": row["jid"], 
                "name": row["name"],
                "last_activity": str(row["last_activity"]) if row["last_activity"] else None
            } 
            for row in rows
        ]
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_database.py::test_get_groups_with_activity -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/backend/database.py tests/test_database.py
git commit -m "feat(db): add days_active filter and last_activity to get_groups"
```

---

### Task 4: Update CLI for Filtering and Output

**Files:**
- Modify: `src/backend/cli.py`

**Step 1: Write the failing test**
```python
# tests/test_cli.py
import argparse
from unittest.mock import patch, MagicMock
from src.backend.cli import list_groups

@patch('src.backend.cli.WhatsAppDB')
def test_list_groups_with_activity(mock_db_class):
    mock_db = mock_db_class.return_value
    mock_db.get_groups.return_value = [
        {"jid": "1@g.us", "name": "Group 1", "last_activity": "2026-03-07 10:00:00"}
    ]
    
    # Should not raise exception
    list_groups(mock_db, days_active=30)
    mock_db.get_groups.assert_called_with(days_active=30)
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_list_groups_with_activity -v`
Expected: FAIL

**Step 3: Write minimal implementation**
```python
# src/backend/cli.py
# Update list_groups:
def list_groups(db: WhatsAppDB, days_active: Optional[int] = None) -> None:
    """List all WhatsApp groups."""
    groups = db.get_groups(days_active=days_active)
    if not groups:
        filter_msg = f" in the last {days_active} days" if days_active else ""
        logger.info(f"No groups found{filter_msg}. Make sure wacli has synced messages.")
        return
    
    logger.info(f"Found {len(groups)} groups:\n")
    for i, group in enumerate(groups, 1):
        activity = f" (Last active: {group['last_activity']})" if group.get('last_activity') else ""
        logger.info(f"  {i}. {group['name']} [{group['jid']}]{activity}")

# Update main() argparse:
    parser.add_argument(
        "--days-active", "-d",
        type=int,
        help="Filter groups active in the last N days (used with --list)"
    )

# Update main() logic:
    if args.list:
        list_groups(db, args.days_active)
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_cli.py::test_list_groups_with_activity -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/backend/cli.py tests/test_cli.py
git commit -m "feat(cli): support --days-active flag and show last activity"
```

---

### Task 5: Integrate spaCy for NLP Parsing

**Files:**
- Modify: `requirements.txt`
- Modify: `src/backend/parser.py`
- Modify: `tests/test_parser.py`

**Step 1: Write the failing test**
```python
# tests/test_parser.py
from src.backend.parser import extract_assignee_nlp, extract_task_nlp

def test_extract_assignee_nlp():
    # Requires spaCy model to be loaded
    assert extract_assignee_nlp("John will send the report") == "John"
    assert extract_assignee_nlp("Please ask Sarah to review this") == "Sarah"

def test_extract_task_nlp():
    assert "send the report" in extract_task_nlp("John will send the report by tomorrow").lower()
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_parser.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**
```bash
# Add to requirements.txt
spacy>=3.7.0
```

```python
# src/backend/parser.py
import spacy
import logging

logger = logging.getLogger(__name__)

# Try to load model, fallback gracefully if not installed
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    logger.warning("spaCy model 'en_core_web_sm' not found. Run: python -m spacy download en_core_web_sm")
    nlp = None

def extract_assignee_nlp(text: str, sender: Optional[str] = None) -> str:
    if not nlp:
        return extract_assignee(text, sender) # Fallback to regex
        
    doc = nlp(text)
    
    # Look for PERSON entities
    persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    if persons:
        return persons[0]
        
    # Look for nominal subjects attached to action verbs
    for token in doc:
        if token.dep_ == "nsubj" and token.head.pos_ == "VERB":
            # Filter out pronouns like "I", "we", "you" unless specific
            if token.text.lower() not in ["i", "we", "you", "they", "he", "she", "it"]:
                return token.text
                
    return extract_assignee(text, sender) # Fallback

def extract_task_nlp(text: str) -> str:
    if not nlp:
        return extract_task(text)
        
    doc = nlp(text)
    
    # Find the main verb (ROOT)
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            # Extract the subtree of the verb (the action phrase)
            subtree = list(token.subtree)
            # Reconstruct the phrase
            phrase = " ".join([t.text for t in subtree])
            # Clean up punctuation
            return phrase.strip(".,!?")
            
    return extract_task(text) # Fallback

# Update parse_message to use NLP functions if available
```

**Step 4: Run test to verify it passes**
Run: 
```bash
pip install spacy
python -m spacy download en_core_web_sm
pytest tests/test_parser.py -v
```
Expected: PASS

**Step 5: Commit**
```bash
git add requirements.txt src/backend/parser.py tests/test_parser.py
git commit -m "feat(parser): integrate spaCy for NLP-based extraction"
```

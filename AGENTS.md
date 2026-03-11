# AGENTS.md - WhatsApp Agent Standards

> **Note to AI Agents:** There are no `.cursorrules` or `.github/copilot-instructions.md` files in this repository. This `AGENTS.md` file is your single source of truth for working in this project.

## Jira Project Anchor
- Primary Project: COST (reusing an existing project)
- Scope: You are only permitted to read/update issues within the COST project.

## Decision Boundaries
- **AUTONOMOUS**: Generating Pydantic models, unit tests, and refactoring logic.
- **CONSULT**: Adding new external libraries.
- **ESCALATE**: Changes to user privacy or data retention policies.

## Technical Rules
- Python: Use 3.12+ features and Pydantic v2+ for validation.
- Quality: Zero "Critical" issues allowed in SonarQube analysis.
- Value Hierarchy: Accuracy > Privacy > UI Consistency > Speed.

## Jira Stage Hierarchy (COST Project)
**Linear Flow:** TODO → Requirement Gathering → Prioritized → Development → Code Review → UAT → QA → QA Approved → Production Deployed

**Available Transitions from TODO:** Evaluating Requirements (61), Requirement Gathering (321), De-prioritized (201), Closed (351)

**Decision Boundaries for Stages:**
- Autonomous: Move a ticket from TO DO to IN PROGRESS.
- Escalate: MUST NOT move a ticket to DONE if SonarQube reports "Critical" vulnerabilities.

---

# Development Guide

## Build, Lint, and Test Commands

### Prerequisites
```bash
source ~/.venv/bin/activate
pip3 install -r requirements.txt
```

### Running Tests
```bash
# Run all tests with coverage
pytest --cov=src/backend --cov-report=xml

# Run a single test file
pytest tests/test_file.py

# Run a single test function
pytest tests/test_file.py::test_function_name

# Run tests with verbose output
pytest -v

# Run tests matching a pattern
pytest -k "test_pattern"

# Generate HTML coverage report
pytest --cov=src/backend --cov-report=term-missing --cov-report=html
```

### Code Quality
```bash
# SonarQube analysis (via CI)
sonar-scanner -Dsonar.projectKey=whatsapp_agent -Dsonar.python.coverage.reportPaths=coverage.xml
```

---

## Code Style Guidelines

### General Principles
- Write clean, readable, and maintainable code
- Follow PEP 8 for Python code style
- Use Python 3.12+ features (match statements, typed dicts, etc.)
- Prioritize accuracy and correctness over clever solutions

### Imports (Order Matters)
```python
# 1. Standard library
from datetime import date
from typing import Optional

# 2. Third-party packages
from pydantic import BaseModel, Field

# 3. Local application
from src.backend.models import ActionableItem
```

### Naming Conventions
| Type | Convention | Example |
|------|------------|---------|
| Files | snake_case | `action_processor.py` |
| Classes | PascalCase | `ActionableItem` |
| Functions/methods | snake_case | `process_message` |
| Constants | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| Variables | snake_case | `user_name` |

### Type Annotations
- Always use type hints for function parameters and return values
- Use `Optional[X]` instead of `X | None`
- Use `None` as default for optional parameters
```python
def process_item(item_id: int, name: str | None = None) -> Optional[ActionableItem]:
    ...
```

### Pydantic Models
- Use Pydantic v2+ (import from `pydantic`)
- Use `BaseModel` for data validation
- Use `Field` for field validation and descriptions
- Add descriptions for all fields for documentation
```python
class ActionableItem(BaseModel):
    task: str = Field(..., description="The verb-noun action")
    assignee: str = Field(default="unassigned")
    deadline: Optional[date] = None
    priority: str = Field(pattern="^(High|Medium|Low)$")
```

### Error Handling
- Use specific exception types
- Include meaningful error messages
- Log errors appropriately (use logging module)
- Never expose sensitive information in error messages

### Code Organization
```
src/
├── backend/          # Backend Python code
│   ├── models.py     # Pydantic models
│   └── processor.py  # Business logic
└── extension/       # Browser extension code
    └── content.js

tests/               # Test files (mirror src structure)
```

### Testing Guidelines
- Write unit tests for all business logic
- Use descriptive test names: `test_<function>_<expected_behavior>`
- Test edge cases and error conditions
- Aim for high test coverage (run `pytest --cov`)
- Place tests in `tests/` directory mirroring src structure

### Git Commit Messages
- Use conventional commit format: `type(scope): description`
- Types: feat, fix, docs, style, refactor, test, chore
- Example: `feat(models): add priority validation`

### Security
- Never commit secrets, API keys, or credentials
- Use environment variables for sensitive configuration
- Validate and sanitize all user inputs
- Follow least privilege principle

# tests/test_parser.py
from src.backend.parser import extract_assignee_nlp, extract_task_nlp


def test_extract_assignee_nlp():
    # Requires spaCy model to be loaded
    assert extract_assignee_nlp("John will send the report") == "John"
    assert extract_assignee_nlp("Please ask Sarah to review this") == "Sarah"


def test_extract_task_nlp():
    assert (
        "send the report"
        in extract_task_nlp("John will send the report by tomorrow").lower()
    )

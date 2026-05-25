"""Pydantic models for wiki compilation operations."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class ConceptPage(BaseModel):
    """A wiki concept page to create or update."""

    filename: str = Field(..., description="Filename without path, e.g. 'school_fees.md'")
    content: str = Field(..., description="Full Markdown content for the page")
    action: str = Field(
        default="create",
        pattern="^(create|update|append)$",
        description="Whether to create, overwrite, or append to the page",
    )


class TaskEntry(BaseModel):
    """A task to add to tasks.md."""

    text: str = Field(..., description="Task description")
    priority: str = Field(default="Medium", pattern="^(High|Medium|Low)$")
    due_date: Optional[str] = Field(default=None, description="YYYY-MM-DD or null")
    category: Optional[str] = Field(default=None)
    source_group: Optional[str] = Field(default=None)


class LogEntry(BaseModel):
    """An entry for the append-only log."""

    summary: str = Field(..., description="What was updated in this compilation")
    pages_affected: list[str] = Field(default_factory=list)


class WikiUpdate(BaseModel):
    """Structured output from LLM wiki compilation."""

    tasks_to_add: list[TaskEntry] = Field(default_factory=list)
    tasks_to_remove: list[str] = Field(
        default_factory=list,
        description="Task text patterns to mark as done",
    )
    concept_pages: list[ConceptPage] = Field(default_factory=list)
    index_additions: list[str] = Field(
        default_factory=list,
        description="New entries to add to index.md links section",
    )
    log_entry: LogEntry = Field(
        default_factory=lambda: LogEntry(summary="No changes")
    )

    def has_changes(self) -> bool:
        return bool(
            self.tasks_to_add
            or self.tasks_to_remove
            or self.concept_pages
            or self.index_additions
        )

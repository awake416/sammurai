from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    AnyUrl,
    field_validator,
    AfterValidator,
)
from typing import Optional, Annotated
from enum import Enum


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TaskCategory(str, Enum):
    SCHOOL = "School"
    BILLS = "Bills"
    COMMUNITY = "Community"
    EVENTS = "Events"
    WORK = "Work"
    OTHER = "Other"


class ResourceType(str, Enum):
    URL = "url"
    DOCUMENT = "document"
    POLL = "poll"
    FORM = "form"
    EVENT = "event"
    IMAGE = "image"
    OTHER = "other"


class Resource(BaseModel):
    type: ResourceType = Field(
        ...,
        description="Type of resource (url, document, poll, form, event, image, or other)",
    )
    value: str = Field(..., description="The URL, document name, or link")
    description: Optional[str] = Field(
        default=None, description="Brief description of the resource"
    )


class Message(BaseModel):
    id: str = Field(..., description="Unique message identifier")
    message: str = Field(..., description="The message text content")
    sender: Optional[str] = Field(default=None, description="Name of the sender")
    timestamp: str = Field(..., description="Message timestamp")
    group_name: Optional[str] = Field(default=None, description="Name of the group")
    group_jid: Optional[str] = Field(default=None, description="JID of the group")
    local_path: Optional[str] = Field(
        default=None, description="Local path to downloaded media"
    )
    media_type: Optional[str] = Field(default=None, description="Type of media")
    filename: Optional[str] = Field(default=None, description="Original filename")


class TopicItem(BaseModel):
    topic: str = Field(..., description="The topic name (e.g., 'Parking', 'Security')")
    summary: str = Field(
        ..., description="2-3 sentence description of what was discussed"
    )
    message_count: int = Field(
        ..., ge=1, description="Number of messages related to this topic"
    )
    sample_messages: list[str] = Field(
        default_factory=list, description="Sample messages for context"
    )


def validate_url(v: str) -> str:
    """Validate URL and enforce HTTPS, or allow local file paths."""
    # Allow local file paths that don't look like URLs
    if "://" not in v and not v.startswith("http"):
        # This is a local file path, accept it
        return v

    # It's a URL, enforce HTTPS
    try:
        url = AnyUrl(v)
        scheme = getattr(url, "scheme", None)
        if scheme != "https":
            raise ValueError(
                f"Insecure URL scheme '{scheme}'. Only https:// is allowed."
            )
        return str(url)
    except Exception as e:
        if isinstance(e, ValueError) and "Insecure URL scheme" in str(e):
            raise
        raise ValueError(f"Invalid or insecure URL: {v}. {str(e)}")


class DocumentSummary(BaseModel):
    resource_url: Annotated[str, AfterValidator(validate_url)] = Field(
        ..., description="URL to the document or resource"
    )
    title: str = Field(..., description="Title of the document")
    summary: str = Field(..., description="Brief summary of the document content")
    key_dates: list[str] = Field(
        default_factory=list, description="Important dates mentioned in the document"
    )


class TopicSummary(BaseModel):
    group_name: str = Field(..., description="Name of the WhatsApp group")
    topics: list[TopicItem] = Field(
        default_factory=list, description="List of topics with message counts"
    )
    document_summaries: list[DocumentSummary] = Field(
        default_factory=list, description="Summaries of shared documents"
    )
    date_range: str = Field(
        ..., min_length=1, description="Time period covered (e.g., 'Last 7 days')"
    )

    @field_validator("date_range", mode="after")
    @classmethod
    def validate_date_range(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("date_range cannot be empty or whitespace only")
        return v


class ActionableItem(BaseModel):
    task: str = Field(
        ...,
        description="A highly descriptive, standalone action item that provides enough context to be understood without the original message",
    )
    category: TaskCategory = Field(
        default=TaskCategory.OTHER, description="The category of the task"
    )
    context: Optional[str] = Field(
        default=None,
        description="A brief 1-2 sentence summary of the background info providing necessary context",
    )
    assignee: str = Field(
        default="unassigned", description="Person responsible for the task"
    )
    deadline: Optional[str] = Field(
        default=None, description="Deadline in YYYY-MM-DD format or urgency keyword"
    )
    priority: Priority = Field(
        default=Priority.MEDIUM, description="Priority level of the task"
    )
    project_phase: Optional[str] = Field(
        default=None, description="Project phase if identified"
    )
    topic_tags: list[str] = Field(
        default_factory=list,
        description="Tags identifying the topics this item belongs to",
    )
    original_message: Optional[str] = Field(
        default=None, description="Original WhatsApp message"
    )
    sender: Optional[str] = Field(default=None, description="Message sender name")
    timestamp: Optional[str] = Field(default=None, description="Message timestamp")
    group_name: Optional[str] = Field(default=None, description="WhatsApp group name")
    group_jid: Optional[str] = Field(default=None, description="WhatsApp group JID")
    resources: list[Resource] = Field(
        default_factory=list, description="Related URLs or documents"
    )
    message_ref: Optional[int] = Field(
        default=None, description="Reference to original message index"
    )

    model_config = ConfigDict()

    @field_validator("topic_tags", mode="after")
    @classmethod
    def validate_topic_tags(cls, v: list[str]) -> list[str]:
        for i, tag in enumerate(v):
            if not tag.strip():
                raise ValueError(
                    f"topic_tags[{i}]: tag cannot be empty or whitespace only, got {tag!r}"
                )
        return v


class ActionableCollection(BaseModel):
    items: list[ActionableItem] = Field(
        default_factory=list, description="Collection of actionable items"
    )
    total_count: int = Field(default=0, description="Total number of items")

    def add_item(self, item: ActionableItem) -> None:
        self.items.append(item)
        self.total_count = len(self.items)

    def get_by_priority(self, priority: Priority) -> list[ActionableItem]:
        return [item for item in self.items if item.priority == priority]

    def get_by_assignee(self, assignee: str) -> list[ActionableItem]:
        return [item for item in self.items if item.assignee == assignee]

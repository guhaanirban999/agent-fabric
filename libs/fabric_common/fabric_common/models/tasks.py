"""Broker task models (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    ROUTING = "routing"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskSubmission(BaseModel):
    task_text: str
    domain: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


class RouteDecision(BaseModel):
    """Structured output of the LLM router. `agent_id` MUST be validated against
    the real candidate set before dispatch — never trust a hallucinated id."""

    agent_id: str
    skill_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    used_tool: bool = False
    decision: "RouteDecision | None" = None
    trace_id: str | None = None


class TaskRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    submission: TaskSubmission
    state: TaskState = TaskState.SUBMITTED
    domain: str | None = None
    decision: RouteDecision | None = None
    result: Any | None = None
    error: str | None = None
    trace_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

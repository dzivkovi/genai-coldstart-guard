from typing import Optional

from pydantic import BaseModel, Field


class UserFeedback(BaseModel):
    thumb_up: Optional[str] = None
    thumb_down: Optional[str] = None
    feedback_text: Optional[str] = None
    conversation_response_id: Optional[str] = None
    route: Optional[str] = None


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    conversation_id: Optional[str] = None
    # The source OpenAPI marks this readOnly, but it is useful for local mock routing.
    route: Optional[str] = Field(
        default=None,
        description=(
            "Mock mode only: selects the simulated outcome (see the request examples "
            "dropdown, e.g. mock:success_fast, mock:cold_start_timeout, "
            "mock:databricks_stopped). Ignored when BACKEND_MODE=databricks."
        ),
    )


class Citation(BaseModel):
    topic_title: Optional[str] = None
    topic_source: Optional[str] = None


class ChatPrediction(BaseModel):
    answer: Optional[str] = ""
    citations: list[Citation] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    latency: Optional[float] = None
    success: Optional[bool] = None
    error_message: Optional[str] = None


class ChatResponsePayload(BaseModel):
    conversation_id: Optional[str] = None
    conversation_response_id: Optional[str] = None
    predictions: list[ChatPrediction] = Field(default_factory=list)
    user_feedback: Optional[UserFeedback] = None
    error: Optional[str] = None
    status: Optional[int] = 200

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
    # The source OpenAPI marks this readOnly, but it drives local dispatch.
    route: Optional[str] = Field(
        default=None,
        description=(
            "Drives dispatch. Leave empty to hit the real serving endpoint. A `mock:*` value "
            "selects a simulated outcome (e.g. mock:success_fast, mock:cold_start_timeout, "
            "mock:databricks_stopped) and is honoured only when the server runs with "
            "MOCK_ENABLED=true. Any other value returns a controlled 400."
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

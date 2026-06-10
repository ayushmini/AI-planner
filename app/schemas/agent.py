from pydantic import BaseModel


class AgentChatRequest(BaseModel):
    prompt: str
    start_after: str | None = None
    slack: float = 0.0
    session_id: str | None = None   # Omit to start a NEW session; pass to CONTINUE an existing one


class AgentDecisionRequest(BaseModel):
    session_id: str


class SessionSummary(BaseModel):
    session_id: str
    title: str
    status: str
    created_at: str

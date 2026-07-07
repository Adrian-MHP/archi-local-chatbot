from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


Role = Literal["user", "assistant"]


class ChatTurn(BaseModel):
    role: Role
    content: str = Field(default="", max_length=20000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    history: List[ChatTurn] = Field(default_factory=list)
    system_prompt: str | None = Field(default=None, max_length=20000)


class ChatResponse(BaseModel):
    answer: str
    used_tools: List[str]

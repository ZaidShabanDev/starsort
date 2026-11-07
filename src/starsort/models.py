from datetime import datetime

from pydantic import BaseModel, Field


class Repo(BaseModel):
    full_name: str
    url: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    language: str | None = None
    stars: int = 0
    updated_at: datetime

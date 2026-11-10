from datetime import datetime

from pydantic import BaseModel, Field

OTHER_CATEGORY = "Other"


class Repo(BaseModel):
    full_name: str
    url: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    language: str | None = None
    stars: int = 0
    updated_at: datetime


class RepoAssignment(BaseModel):
    """The category chosen for one repository."""

    full_name: str = Field(description="The repository's full_name exactly as given in the input, e.g. 'owner/name'")
    category: str = Field(description="Short Title Case category name, e.g. 'AI Agents' or 'Shaders & Graphics'")


class BatchCategorization(BaseModel):
    """Category assignments for one batch of GitHub repositories."""

    assignments: list[RepoAssignment] = Field(description="Exactly one entry for every repository in the input")

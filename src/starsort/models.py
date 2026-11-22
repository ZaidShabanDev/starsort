from datetime import datetime

from pydantic import BaseModel, Field

OTHER_CATEGORY = "Other"


class Repo(BaseModel):
    full_name: str
    node_id: str | None = None  # GraphQL ID, needed to add the repo to GitHub Lists
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


class CategoryMerge(BaseModel):
    """Categories a person would see as the same folder."""

    names: list[str] = Field(description="Two or more category names from the input, copied exactly")
    into: str = Field(description="The one name from `names` to keep: the broader, clearer one")


class CategoryMerges(BaseModel):
    """Near-duplicate categories to merge. Empty when every category is distinct."""

    merges: list[CategoryMerge] = Field(description="One entry per group of categories to merge; empty if none")

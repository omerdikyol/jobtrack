"""Validated HTTP request contracts, shared with the background job service."""

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    dry_run: bool = False
    recheck: bool = False
    since: str = Field(default="365", description="Days, duration (3m, 2y) or a date")
    before: str | None = Field(default=None, description="Optional upper date bound")
    limit: int = Field(default=0, ge=0)
    query: str | None = None
    track_sent: bool | None = Field(
        default=None, description="Also record applications the user sent themselves"
    )


class ApplicationUpdate(BaseModel):
    company: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=20000)
    follow_up_on: str | None = None
    status_override: str | None = None
    category: str | None = None


class ReviewModel(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)


class ProviderConnection(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)
    base_url: str | None = Field(default=None, max_length=2048)


class SettingsUpdate(BaseModel):
    """Partial update. Fields left out are unchanged.

    ``api_key`` is special: absent means "leave the stored key alone", an empty
    string means "forget it". The key is never sent back to the browser.
    """

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_mode: str | None = None
    sync_since: str | None = None
    track_sent: bool | None = None
    api_key: str | None = None
    connections: dict[str, ProviderConnection] | None = None
    review_models: list[ReviewModel] | None = Field(default=None, max_length=5)
    review_strategy: str | None = None
    review_rounds: int | None = Field(default=None, ge=1, le=3)

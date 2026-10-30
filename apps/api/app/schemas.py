from datetime import datetime

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: str
    login: str
    name: str
    avatar_url: str

    model_config = {"from_attributes": True}


class DevLogin(BaseModel):
    login: str = Field(min_length=1, max_length=80)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    cli_token: str
    ai_threshold: float
    role: str = "member"


class MemberIn(BaseModel):
    login: str


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    state: str = "todo"


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    state: str | None = None
    priority: str | None = None
    owner_id: str | None = None
    labels: list[str] | None = None


class TaskOut(BaseModel):
    id: str
    workspace_id: str
    number: int
    title: str
    description: str
    state: str
    owner_id: str | None
    owner_label: str
    priority: str
    labels: list[str]
    git_branch: str | None
    repo_full_name: str | None
    pr_number: int | None
    pr_url: str | None
    checks_status: str
    changed_files: list[str]
    reviewer_logins: list[str]
    state_reason: str
    state_source: str
    linked_to_id: str | None
    last_activity_at: datetime
    last_push_at: datetime | None
    last_review_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SignalOut(BaseModel):
    id: str
    workspace_id: str
    source: str
    summary: str
    matched_task_id: str | None
    dismissed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class GitHubConnectionIn(BaseModel):
    installation_id: int | None = None
    account_login: str = ""
    repos: list[str]


class GitHubConnectionOut(BaseModel):
    installation_id: int | None
    account_login: str
    repos: list[str]
    live: bool


class ImportResult(BaseModel):
    created: int
    updated: int
    mode: str


class SimulateIn(BaseModel):
    event: str
    payload: dict


class DeliveryRow(BaseModel):
    id: str
    number: int
    title: str
    state: str
    owner_label: str
    checks_status: str
    pr_number: int | None
    stall_reason: str | None


class DeliveryOut(BaseModel):
    in_progress: list[DeliveryRow]
    in_review: list[DeliveryRow]
    done: list[DeliveryRow]
    blocked: list[DeliveryRow]


class StandupOut(BaseModel):
    text: str


class JiraIn(BaseModel):
    site: str
    email: str
    api_token: str
    project_key: str
    status_map: dict[str, str]


class JiraOut(BaseModel):
    site: str
    email: str
    project_key: str
    status_map: dict[str, str]
    connected: bool


class SuggestionOut(BaseModel):
    id: str
    task_id: str | None
    source_task_id: str | None
    repo_full_name: str
    pr_number: int | None
    pr_title: str
    confidence: float
    reason: str
    status: str

    model_config = {"from_attributes": True}


class NowOut(BaseModel):
    yours: list[TaskOut]
    reviews: list[TaskOut]
    failing: list[TaskOut]


class LinkIn(BaseModel):
    branch: str
    task_number: int | None = None

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    github_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    avatar_url: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    cli_token: Mapped[str] = mapped_column(String(64), default=new_id)
    ai_threshold: Mapped[float] = mapped_column(default=0.4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="workspace")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20), default="member")

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="todo")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    owner_label: Mapped[str] = mapped_column(String(120), default="")
    priority: Mapped[str] = mapped_column(String(20), default="none")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    git_branch: Mapped[str | None] = mapped_column(String(300), nullable=True)
    repo_full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    checks_status: Mapped[str] = mapped_column(String(20), default="none")
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    reviewer_logins: Mapped[list] = mapped_column(JSON, default=list)
    state_reason: Mapped[str] = mapped_column(String(300), default="")
    state_source: Mapped[str] = mapped_column(String(20), default="manual")
    linked_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(400))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    dismissed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    account_login: Mapped[str] = mapped_column(String(120), default="")
    repos: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JiraConnection(Base):
    __tablename__ = "jira_connections"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    site: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(200))
    api_token: Mapped[str] = mapped_column(Text)
    project_key: Mapped[str] = mapped_column(String(32))
    status_map: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncAnchor(Base):
    __tablename__ = "sync_anchors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    jira_key: Mapped[str] = mapped_column(String(32), index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), default="ok")
    conflict_resolution: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    origin: Mapped[str] = mapped_column(String(64), default="")


class LinkSuggestion(Base):
    __tablename__ = "link_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    repo_full_name: Mapped[str] = mapped_column(String(200), default="")
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_title: Mapped[str] = mapped_column(String(300), default="")
    confidence: Mapped[float] = mapped_column(default=0)
    reason: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

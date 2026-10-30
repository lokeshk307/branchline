from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ai_link import TaskRef, heuristic_link, next_threshold
from app.domain.delivery import DeliveryTask, stall_reason, standup_text
from app.domain.github_rules import Intent, intents_from_import
from app.domain.jira_sync import (
    ORIGIN_PROPERTY,
    JiraRateLimit,
    call_with_backoff,
    content_hash,
    inbound_fields,
    outbound_issue,
    should_drop_inbound,
    snapshot_fields,
    validate_status_map,
)
from app.domain.source import github_adapter
from app.hub import hub
from app.models import (
    GitHubInstallation,
    JiraConnection,
    LinkSuggestion,
    Membership,
    Signal,
    SyncAnchor,
    Task,
    User,
    Workspace,
    utcnow,
)

OPEN_STATES = ("todo", "in_progress", "in_review")


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def member_role(db: Session, workspace_id: str, user_id: str) -> Membership | None:
    return db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )


def next_number(db: Session, workspace_id: str) -> int:
    current = db.scalars(select(Task.number).where(Task.workspace_id == workspace_id)).all()
    return (max(current) if current else 0) + 1


def find_task(db: Session, workspace_id: str, intent: Intent) -> Task | None:
    query = select(Task).where(Task.workspace_id == workspace_id)
    if intent.repo:
        query = query.where(Task.repo_full_name == intent.repo)
    rows = db.scalars(query).all()
    if intent.pr_number is not None:
        for task in rows:
            if task.pr_number == intent.pr_number:
                return task
    if intent.branch:
        for task in rows:
            if task.git_branch == intent.branch:
                return task
    return None


def add_signal(
    db: Session,
    workspace_id: str,
    source: str,
    summary: str,
    payload: dict,
    task_id: str | None,
) -> Signal:
    signal = Signal(
        workspace_id=workspace_id,
        source=source,
        summary=summary,
        payload=payload,
        matched_task_id=task_id,
    )
    db.add(signal)
    return signal


def publish_task(task: Task) -> None:
    hub.publish(task.workspace_id, {"type": "task", "task_id": task.id, "state": task.state})


def apply_intent(db: Session, workspace_id: str, intent: Intent, payload: dict) -> Task | None:
    task = find_task(db, workspace_id, intent)
    now = utcnow()
    if task is None and intent.create_if_missing:
        task = Task(
            workspace_id=workspace_id,
            number=next_number(db, workspace_id),
            title=intent.title or intent.branch or "Untitled",
            description=intent.description or "",
            state=intent.state or "in_progress",
            git_branch=intent.branch,
            repo_full_name=intent.repo,
            pr_number=intent.pr_number,
            pr_url=intent.pr_url,
            changed_files=intent.changed_files,
            reviewer_logins=intent.reviewer_logins,
            checks_status=intent.checks_status or "none",
            state_reason=intent.reason,
            state_source="git",
            last_activity_at=now,
            last_push_at=now if intent.touch == "push" else None,
            last_review_at=now if intent.touch == "review" else None,
        )
        db.add(task)
        db.flush()
        add_signal(db, workspace_id, "github", intent.reason, payload, task.id)
        return task
    if task is None:
        if intent.inbox_if_missing:
            add_signal(
                db,
                workspace_id,
                "github",
                intent.inbox_summary or intent.reason,
                payload,
                None,
            )
        return None
    if intent.state:
        task.state = intent.state
    if intent.title and not task.title:
        task.title = intent.title
    if intent.title and task.state_source == "git" and task.title == task.git_branch:
        task.title = intent.title
    if intent.description and not task.description:
        task.description = intent.description
    if intent.branch:
        task.git_branch = intent.branch
    if intent.repo:
        task.repo_full_name = intent.repo
    if intent.pr_number is not None:
        task.pr_number = intent.pr_number
    if intent.pr_url:
        task.pr_url = intent.pr_url
    if intent.changed_files:
        task.changed_files = intent.changed_files
    if intent.reviewer_logins:
        task.reviewer_logins = intent.reviewer_logins
    if intent.checks_status:
        task.checks_status = intent.checks_status
    if intent.reason:
        task.state_reason = intent.reason
    task.state_source = "git"
    task.last_activity_at = now
    task.updated_at = now
    if intent.touch == "push":
        task.last_push_at = now
    if intent.touch == "review":
        task.last_review_at = now
    add_signal(db, workspace_id, "github", intent.reason or "GitHub update", payload, task.id)
    return task


def apply_github_event(db: Session, workspace_id: str, event: str, payload: dict) -> Task | None:
    intent = github_adapter.parse(event, payload)
    if intent is None:
        return None
    task = apply_intent(db, workspace_id, intent, payload)
    if task is not None and event == "pull_request" and payload.get("action") in {
        "opened",
        "reopened",
        "ready_for_review",
    }:
        remember_suggestion(db, workspace_id, task, payload["pull_request"].get("title") or "", payload["pull_request"].get("body") or "")
    db.commit()
    if task is not None:
        publish_task(task)
        maybe_push_jira(db, task)
    return task


def import_known_pulls(
    db: Session,
    workspace_id: str,
    open_prs: list[dict],
    merged_prs: list[dict],
) -> dict:
    created = 0
    updated = 0
    for intent in intents_from_import(open_prs, merged_prs):
        before = find_task(db, workspace_id, intent)
        task = apply_intent(db, workspace_id, intent, {"import": True, "pr": intent.pr_number})
        if task is None:
            continue
        if before is None:
            created += 1
        else:
            updated += 1
        if intent.state == "in_review":
            remember_suggestion(db, workspace_id, task, intent.title or "", intent.description or "")
    db.commit()
    for task in db.scalars(select(Task).where(Task.workspace_id == workspace_id)).all():
        publish_task(task)
    return {"created": created, "updated": updated}


def create_task(
    db: Session,
    workspace: Workspace,
    user: User,
    title: str,
    description: str = "",
    state: str = "todo",
) -> Task:
    task = Task(
        workspace_id=workspace.id,
        number=next_number(db, workspace.id),
        title=title,
        description=description,
        state=state,
        owner_id=user.id,
        owner_label=user.login,
        state_reason=f"Created by {user.login}",
        state_source="manual",
    )
    db.add(task)
    db.flush()
    add_signal(db, workspace.id, "manual", task.state_reason, {}, task.id)
    db.commit()
    publish_task(task)
    return task


def update_task(db: Session, task: Task, user: User, changes: dict) -> Task:
    previous_state = task.state
    for key in ("title", "description", "state", "priority", "owner_id", "labels"):
        if key in changes and changes[key] is not None:
            setattr(task, key, changes[key])
    if "owner_id" in changes:
        owner = db.get(User, changes["owner_id"]) if changes["owner_id"] else None
        task.owner_label = owner.login if owner else ""
        task.owner_id = changes["owner_id"]
    if changes.get("state") and changes["state"] != previous_state:
        task.state_source = "manual"
        task.state_reason = f"Moved by {user.login}"
        add_signal(db, task.workspace_id, "manual", task.state_reason, {"state": task.state}, task.id)
    task.updated_at = utcnow()
    task.last_activity_at = task.updated_at
    db.commit()
    publish_task(task)
    maybe_push_jira(db, task)
    return task


def delivery_for(db: Session, workspace_id: str, now: datetime | None = None) -> dict:
    now = aware(now or utcnow()) or utcnow()
    week = now - timedelta(days=7)
    tasks = db.scalars(select(Task).where(Task.workspace_id == workspace_id)).all()
    grouped: dict[str, list] = {"in_progress": [], "in_review": [], "done": [], "blocked": []}
    for task in tasks:
        view = _delivery_task(task)
        reason = stall_reason(view, now)
        if task.state == "done" and (aware(task.updated_at) or now) >= week:
            grouped["done"].append(_row(task, reason))
        elif task.state in {"in_progress", "in_review"}:
            grouped[task.state].append(_row(task, reason))
        if reason and task.state != "done":
            grouped["blocked"].append(_row(task, reason))
    return grouped


def standup_for(db: Session, workspace_id: str, now: datetime | None = None) -> str:
    now = now or utcnow()
    grouped = delivery_for(db, workspace_id, now)
    pairs: dict[str, list] = {}
    for key, rows in grouped.items():
        pairs[key] = [
            (
                DeliveryTask(
                    number=row["number"],
                    title=row["title"],
                    state=row["state"],
                    owner_label=row["owner_label"],
                    checks_status=row["checks_status"],
                    last_push_at=None,
                    last_review_at=None,
                    created_at=now,
                    updated_at=now,
                    pr_number=row["pr_number"],
                ),
                row["stall_reason"],
            )
            for row in rows
        ]
    return standup_text(pairs, now.strftime("%-d %b"))


def _delivery_task(task: Task) -> DeliveryTask:
    return DeliveryTask(
        number=task.number,
        title=task.title,
        state=task.state,
        owner_label=task.owner_label,
        checks_status=task.checks_status,
        last_push_at=aware(task.last_push_at),
        last_review_at=aware(task.last_review_at),
        created_at=aware(task.created_at) or utcnow(),
        updated_at=aware(task.updated_at) or utcnow(),
        pr_number=task.pr_number,
    )


def _row(task: Task, reason: str | None) -> dict:
    return {
        "id": task.id,
        "number": task.number,
        "title": task.title,
        "state": task.state,
        "owner_label": task.owner_label,
        "checks_status": task.checks_status,
        "pr_number": task.pr_number,
        "stall_reason": reason,
    }


def remember_suggestion(
    db: Session,
    workspace_id: str,
    source: Task,
    pr_title: str,
    pr_body: str,
) -> LinkSuggestion | None:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return None
    others = [
        TaskRef(id=task.id, number=task.number, title=task.title)
        for task in db.scalars(
            select(Task).where(Task.workspace_id == workspace_id, Task.id != source.id, Task.state.in_(OPEN_STATES))
        ).all()
    ]
    decision = heuristic_link(pr_title, pr_body, others)
    if decision is None or decision.task_id is None or decision.confidence < 0.15:
        return None
    existing = db.scalar(
        select(LinkSuggestion).where(
            LinkSuggestion.workspace_id == workspace_id,
            LinkSuggestion.source_task_id == source.id,
            LinkSuggestion.task_id == decision.task_id,
            LinkSuggestion.status == "pending",
        )
    )
    if existing:
        return existing
    suggestion = LinkSuggestion(
        workspace_id=workspace_id,
        task_id=decision.task_id,
        source_task_id=source.id,
        repo_full_name=source.repo_full_name or "",
        pr_number=source.pr_number,
        pr_title=pr_title,
        confidence=decision.confidence,
        reason=decision.reason,
        status="pending",
    )
    db.add(suggestion)
    return suggestion


def decide_suggestion(db: Session, suggestion: LinkSuggestion, accepted: bool) -> LinkSuggestion:
    workspace = db.get(Workspace, suggestion.workspace_id)
    suggestion.status = "accepted" if accepted else "rejected"
    if workspace is not None:
        workspace.ai_threshold = next_threshold(workspace.ai_threshold, suggestion.confidence, accepted)
    if accepted and suggestion.task_id and suggestion.source_task_id:
        source = db.get(Task, suggestion.source_task_id)
        target = db.get(Task, suggestion.task_id)
        if source is not None:
            source.linked_to_id = suggestion.task_id
            add_signal(
                db,
                suggestion.workspace_id,
                "manual",
                f"Accepted link to BL-{target.number if target else '?'}",
                {},
                source.id,
            )
    db.commit()
    return suggestion


def task_fields(task: Task) -> dict:
    return snapshot_fields(
        title=task.title,
        description=task.description,
        state=task.state,
        priority=task.priority,
        labels=list(task.labels or []),
        assignee=task.owner_label,
    )


def maybe_push_jira(db: Session, task: Task) -> None:
    anchor = db.scalar(select(SyncAnchor).where(SyncAnchor.task_id == task.id))
    connection = db.get(JiraConnection, task.workspace_id)
    if anchor is None or connection is None:
        return
    from app.worker import enqueue_jira_push

    enqueue_jira_push(task.workspace_id, task.id)


def push_task_to_jira(db: Session, workspace_id: str, task_id: str, client: httpx.Client | None = None) -> None:
    task = db.get(Task, task_id)
    connection = db.get(JiraConnection, workspace_id)
    anchor = db.scalar(select(SyncAnchor).where(SyncAnchor.task_id == task_id))
    if task is None or connection is None or anchor is None:
        return
    fields = task_fields(task)
    digest = content_hash(fields)
    body = outbound_issue(fields, connection.status_map, task.id)
    owns_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        def send():
            response = client.put(
                f"{connection.site.rstrip('/')}/rest/api/3/issue/{anchor.jira_key}",
                json={"fields": _jira_fields(body), "properties": [{"key": ORIGIN_PROPERTY, "value": digest}]},
                auth=(connection.email, connection.api_token),
            )
            if response.status_code == 429:
                raise JiraRateLimit()
            response.raise_for_status()
            return response

        call_with_backoff(send)
    except Exception as exc:
        anchor.sync_status = "error"
        anchor.conflict_resolution = str(exc)[:500]
        anchor.last_synced_at = utcnow()
        db.commit()
        return
    finally:
        if owns_client:
            client.close()
    anchor.content_hash = digest
    anchor.origin = digest
    anchor.sync_status = "ok"
    anchor.last_synced_at = utcnow()
    db.commit()


def _jira_fields(body: dict) -> dict:
    return {
        "summary": body["summary"],
        "description": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": body["description"] or ""}]}],
        },
        "labels": body["labels"],
    }


def apply_jira_issue(db: Session, workspace_id: str, issue: dict) -> str:
    connection = db.get(JiraConnection, workspace_id)
    if connection is None:
        return "ignored"
    key = issue["key"]
    anchor = db.scalar(
        select(SyncAnchor).where(SyncAnchor.workspace_id == workspace_id, SyncAnchor.jira_key == key)
    )
    incoming = inbound_fields(issue, connection.status_map)
    incoming_hash = content_hash(incoming)
    origin = issue.get("origin")
    if anchor is not None and should_drop_inbound(anchor.content_hash, incoming_hash, origin):
        return "dropped"
    if anchor is None:
        task = Task(
            workspace_id=workspace_id,
            number=next_number(db, workspace_id),
            title=incoming["title"] or key,
            description=incoming["description"],
            state=incoming["state"],
            priority=incoming["priority"],
            labels=incoming["labels"],
            owner_label=incoming["assignee"],
            state_reason=f"Imported from {key}",
            state_source="manual",
        )
        db.add(task)
        db.flush()
        anchor = SyncAnchor(
            workspace_id=workspace_id,
            task_id=task.id,
            jira_key=key,
            content_hash=incoming_hash,
            origin=origin or "",
            sync_status="ok",
            last_synced_at=utcnow(),
        )
        db.add(anchor)
        add_signal(db, workspace_id, "jira", f"Imported from {key}", issue, task.id)
        db.commit()
        publish_task(task)
        return "imported"
    task = db.get(Task, anchor.task_id)
    if task is None:
        return "ignored"
    losing = task_fields(task)
    if content_hash(losing) != incoming_hash:
        anchor.conflict_resolution = str(losing)
        anchor.sync_status = "conflict"
    task.title = incoming["title"] or task.title
    task.description = incoming["description"]
    task.state = incoming["state"]
    task.priority = incoming["priority"]
    task.labels = incoming["labels"]
    task.owner_label = incoming["assignee"]
    task.state_reason = f"Updated from {key}"
    task.updated_at = utcnow()
    anchor.content_hash = incoming_hash
    anchor.origin = origin or anchor.origin
    anchor.last_synced_at = utcnow()
    add_signal(db, workspace_id, "jira", task.state_reason, issue, task.id)
    db.commit()
    publish_task(task)
    return "updated"


def connect_jira(
    db: Session,
    workspace_id: str,
    site: str,
    email: str,
    api_token: str,
    project_key: str,
    status_map: dict,
) -> JiraConnection:
    validate_status_map(status_map)
    existing = db.get(JiraConnection, workspace_id)
    if existing is None:
        existing = JiraConnection(workspace_id=workspace_id, site=site, email=email, api_token=api_token, project_key=project_key, status_map=status_map)
        db.add(existing)
    else:
        existing.site = site
        existing.email = email
        existing.api_token = api_token
        existing.project_key = project_key
        existing.status_map = status_map
    db.commit()
    return existing


def fetch_jira_issues(connection: JiraConnection, client: httpx.Client) -> list[dict]:
    def send():
        response = client.get(
            f"{connection.site.rstrip('/')}/rest/api/3/search/jql",
            params={"jql": f"project = {connection.project_key}", "fields": "summary,description,status,priority,labels,assignee"},
            auth=(connection.email, connection.api_token),
        )
        if response.status_code == 429:
            raise JiraRateLimit()
        response.raise_for_status()
        return response.json()

    payload = call_with_backoff(send)
    issues = []
    for issue in payload.get("issues", []):
        fields = issue.get("fields") or {}
        issues.append(
            {
                "key": issue["key"],
                "summary": fields.get("summary") or "",
                "description": _plain_description(fields.get("description")),
                "status": (fields.get("status") or {}).get("name") or "",
                "priority": (fields.get("priority") or {}).get("name") or "",
                "labels": fields.get("labels") or [],
                "assignee": ((fields.get("assignee") or {}).get("displayName")) or "",
                "origin": None,
            }
        )
    return issues


def _plain_description(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    texts: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and "text" in node:
                texts.append(node["text"])
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return "\n".join(texts)

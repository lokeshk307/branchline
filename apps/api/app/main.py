import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import httpx
import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import create_all, get_db
from app.github_client import installation_token, list_repo_pulls
from app.models import GitHubInstallation, JiraConnection, LinkSuggestion, Membership, Signal, Task, User, Workspace
from app.schemas import (
    DeliveryOut,
    DevLogin,
    GitHubConnectionIn,
    GitHubConnectionOut,
    ImportResult,
    JiraIn,
    JiraOut,
    LinkIn,
    MemberIn,
    NowOut,
    SignalOut,
    SimulateIn,
    StandupOut,
    SuggestionOut,
    TaskIn,
    TaskOut,
    TaskPatch,
    UserOut,
    WorkspaceIn,
    WorkspaceOut,
)
from app.services import (
    apply_github_event,
    apply_jira_issue,
    connect_jira,
    create_task,
    decide_suggestion,
    delivery_for,
    fetch_jira_issues,
    import_known_pulls,
    member_role,
    standup_for,
    update_task,
)
from app.worker import enqueue_github
from app.hub import hub

settings = get_settings()
if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0)

app = FastAPI(title="Branchline", version="0.1.0")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    create_all()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in")
    return user


def load_workspace(workspace_id: str, user: User, db: Session) -> Workspace:
    if member_role(db, workspace_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def workspace_out(db: Session, workspace: Workspace, user: User) -> WorkspaceOut:
    role = member_role(db, workspace.id, user.id)
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        cli_token=workspace.cli_token,
        ai_threshold=workspace.ai_threshold,
        role=role.role if role else "member",
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/auth/dev", response_model=UserOut)
def dev_login(body: DevLogin, request: Request, db: Session = Depends(get_db)) -> User:
    login = body.login.strip().lower()
    user = db.scalar(select(User).where(User.login == login))
    if user is None:
        user = User(login=login, name=login)
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = user.id
    return user


@app.post("/api/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/github/login")
def github_login() -> dict:
    if not settings.github_client_id:
        raise HTTPException(status_code=400, detail="GitHub OAuth is not configured")
    redirect = settings.public_api_url.rstrip("/") + "/api/auth/github/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}&scope=read:user user:email&redirect_uri={redirect}"
    )
    return {"url": url}


@app.get("/api/auth/github/callback")
def github_callback(code: str, request: Request, db: Session = Depends(get_db)):
    if not settings.github_client_id:
        raise HTTPException(status_code=400, detail="GitHub OAuth is not configured")
    token_response = httpx.post(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    token_response.raise_for_status()
    access = token_response.json().get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="GitHub did not return a token")
    profile = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
        timeout=20,
    )
    profile.raise_for_status()
    data = profile.json()
    github_id = str(data["id"])
    user = db.scalar(select(User).where(User.github_id == github_id))
    if user is None:
        user = User(
            github_id=github_id,
            login=data["login"],
            name=data.get("name") or data["login"],
            avatar_url=data.get("avatar_url") or "",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = user.id
    target = settings.cors_origin_list[0] if settings.cors_origin_list else "/"
    return RedirectResponse(target)


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.get("/api/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[WorkspaceOut]:
    rows = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    result = []
    for membership in rows:
        workspace = db.get(Workspace, membership.workspace_id)
        if workspace:
            result.append(workspace_out(db, workspace, user))
    return result


@app.post("/api/workspaces", response_model=WorkspaceOut)
def make_workspace(body: WorkspaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> WorkspaceOut:
    workspace = Workspace(name=body.name.strip())
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(workspace)
    return workspace_out(db, workspace, user)


@app.post("/api/workspaces/{workspace_id}/members", response_model=UserOut)
def add_member(
    workspace_id: str,
    body: MemberIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> User:
    workspace = load_workspace(workspace_id, user, db)
    role = member_role(db, workspace.id, user.id)
    if role is None or role.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can add a member")
    login = body.login.strip().lower()
    member = db.scalar(select(User).where(User.login == login))
    if member is None:
        member = User(login=login, name=login)
        db.add(member)
        db.flush()
    if member_role(db, workspace.id, member.id) is None:
        db.add(Membership(workspace_id=workspace.id, user_id=member.id, role="member"))
    db.commit()
    return member


@app.get("/api/workspaces/{workspace_id}/members", response_model=list[UserOut])
def list_members(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[User]:
    load_workspace(workspace_id, user, db)
    rows = db.scalars(select(Membership).where(Membership.workspace_id == workspace_id)).all()
    return [row.user for row in rows]


@app.get("/api/workspaces/{workspace_id}/tasks", response_model=list[TaskOut])
def list_tasks(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Task]:
    load_workspace(workspace_id, user, db)
    return list(db.scalars(select(Task).where(Task.workspace_id == workspace_id).order_by(Task.number)).all())


@app.post("/api/workspaces/{workspace_id}/tasks", response_model=TaskOut)
def post_task(
    workspace_id: str,
    body: TaskIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Task:
    workspace = load_workspace(workspace_id, user, db)
    if body.state not in {"todo", "in_progress", "in_review", "done"}:
        raise HTTPException(status_code=400, detail="Unknown state")
    return create_task(db, workspace, user, body.title.strip(), body.description, body.state)


@app.patch("/api/tasks/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: str,
    body: TaskPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = db.get(Task, task_id)
    if task is None or member_role(db, task.workspace_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    changes = body.model_dump(exclude_unset=True)
    if "state" in changes and changes["state"] not in {"todo", "in_progress", "in_review", "done"}:
        raise HTTPException(status_code=400, detail="Unknown state")
    return update_task(db, task, user, changes)


@app.get("/api/tasks/{task_id}/signals", response_model=list[SignalOut])
def task_signals(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Signal]:
    task = db.get(Task, task_id)
    if task is None or member_role(db, task.workspace_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return list(
        db.scalars(select(Signal).where(Signal.matched_task_id == task_id).order_by(Signal.created_at)).all()
    )


@app.get("/api/workspaces/{workspace_id}/inbox", response_model=list[SignalOut])
def inbox(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Signal]:
    load_workspace(workspace_id, user, db)
    return list(
        db.scalars(
            select(Signal).where(
                Signal.workspace_id == workspace_id,
                Signal.matched_task_id.is_(None),
                Signal.dismissed.is_(False),
            )
        ).all()
    )


@app.post("/api/signals/{signal_id}/dismiss", response_model=SignalOut)
def dismiss_signal(signal_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Signal:
    signal = db.get(Signal, signal_id)
    if signal is None or member_role(db, signal.workspace_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.dismissed = True
    db.commit()
    return signal


@app.get("/api/workspaces/{workspace_id}/now", response_model=NowOut)
def now_view(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> NowOut:
    load_workspace(workspace_id, user, db)
    tasks = db.scalars(select(Task).where(Task.workspace_id == workspace_id)).all()
    yours = [task for task in tasks if task.owner_id == user.id and task.state != "done"]
    reviews = [
        task
        for task in tasks
        if task.state == "in_review" and user.login in (task.reviewer_logins or [])
    ]
    failing = [task for task in tasks if task.checks_status == "failing" and task.owner_id == user.id]
    return NowOut(yours=yours, reviews=reviews, failing=failing)


@app.get("/api/workspaces/{workspace_id}/delivery", response_model=DeliveryOut)
def delivery(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    load_workspace(workspace_id, user, db)
    return delivery_for(db, workspace_id)


@app.get("/api/workspaces/{workspace_id}/standup", response_model=StandupOut)
def standup(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> StandupOut:
    load_workspace(workspace_id, user, db)
    return StandupOut(text=standup_for(db, workspace_id))


@app.get("/api/workspaces/{workspace_id}/export")
def export_tasks(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    load_workspace(workspace_id, user, db)
    tasks = db.scalars(select(Task).where(Task.workspace_id == workspace_id).order_by(Task.number)).all()
    return {"tasks": [TaskOut.model_validate(task).model_dump(mode="json") for task in tasks]}


@app.get("/api/workspaces/{workspace_id}/github", response_model=GitHubConnectionOut)
def github_state(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> GitHubConnectionOut:
    load_workspace(workspace_id, user, db)
    row = db.scalar(select(GitHubInstallation).where(GitHubInstallation.workspace_id == workspace_id))
    if row is None:
        return GitHubConnectionOut(installation_id=None, account_login="", repos=[], live=False)
    return GitHubConnectionOut(
        installation_id=row.installation_id,
        account_login=row.account_login,
        repos=list(row.repos or []),
        live=bool(settings.github_app_id and row.installation_id),
    )


@app.get("/api/workspaces/{workspace_id}/github/install-url")
def github_install_url(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    load_workspace(workspace_id, user, db)
    if not settings.github_app_slug:
        return {"url": ""}
    return {"url": f"https://github.com/apps/{settings.github_app_slug}/installations/new"}


@app.post("/api/workspaces/{workspace_id}/github", response_model=GitHubConnectionOut)
def save_github(
    workspace_id: str,
    body: GitHubConnectionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> GitHubConnectionOut:
    load_workspace(workspace_id, user, db)
    if not body.repos:
        raise HTTPException(status_code=400, detail="Pick at least one repo")
    row = db.scalar(select(GitHubInstallation).where(GitHubInstallation.workspace_id == workspace_id))
    if row is None:
        row = GitHubInstallation(workspace_id=workspace_id)
        db.add(row)
    row.installation_id = body.installation_id
    row.account_login = body.account_login
    row.repos = body.repos
    db.commit()
    return GitHubConnectionOut(
        installation_id=row.installation_id,
        account_login=row.account_login,
        repos=list(row.repos),
        live=bool(settings.github_app_id and row.installation_id),
    )


@app.post("/api/workspaces/{workspace_id}/github/import", response_model=ImportResult)
def github_import(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ImportResult:
    load_workspace(workspace_id, user, db)
    row = db.scalar(select(GitHubInstallation).where(GitHubInstallation.workspace_id == workspace_id))
    if row is None or not row.repos:
        raise HTTPException(status_code=400, detail="Pick at least one repo")
    if not settings.github_app_id or not row.installation_id:
        return ImportResult(created=0, updated=0, mode="waiting")
    token = installation_token(row.installation_id)
    open_prs: list[dict] = []
    merged: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with httpx.Client(timeout=30) as client:
        for repo in row.repos:
            repo_open, repo_merged = list_repo_pulls(token, repo, client)
            open_prs.extend(repo_open)
            for pr in repo_merged:
                merged_at = pr.get("merged_at")
                if merged_at and datetime.fromisoformat(merged_at.replace("Z", "+00:00")) >= cutoff:
                    merged.append(pr)
    result = import_known_pulls(db, workspace_id, open_prs, merged)
    mode = "imported" if result["created"] or result["updated"] else "empty"
    return ImportResult(created=result["created"], updated=result["updated"], mode=mode)


@app.post("/api/workspaces/{workspace_id}/github/simulate", response_model=TaskOut | None)
def github_simulate(
    workspace_id: str,
    body: SimulateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Task | None:
    if not settings.allow_simulate:
        raise HTTPException(status_code=404, detail="Not found")
    load_workspace(workspace_id, user, db)
    task = apply_github_event(db, workspace_id, body.event, body.payload)
    return task


@app.post("/api/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await request.body()
    header = request.headers.get("x-hub-signature-256", "")
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=400, detail="Webhook secret is not configured")
    digest = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={digest}", header):
        raise HTTPException(status_code=401, detail="Bad signature")
    payload = await request.json()
    event = request.headers.get("x-github-event", "")
    installation_id = (payload.get("installation") or {}).get("id")
    row = db.scalar(select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id))
    if row is None:
        return {"ok": True, "ignored": True}
    enqueue_github(row.workspace_id, event, payload)
    return {"ok": True}


@app.get("/api/workspaces/{workspace_id}/jira", response_model=JiraOut)
def jira_state(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> JiraOut:
    load_workspace(workspace_id, user, db)
    row = db.get(JiraConnection, workspace_id)
    if row is None:
        return JiraOut(site="", email="", project_key="", status_map={}, connected=False)
    return JiraOut(site=row.site, email=row.email, project_key=row.project_key, status_map=row.status_map, connected=True)


@app.post("/api/workspaces/{workspace_id}/jira", response_model=JiraOut)
def jira_connect(
    workspace_id: str,
    body: JiraIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JiraOut:
    load_workspace(workspace_id, user, db)
    try:
        row = connect_jira(db, workspace_id, body.site, body.email, body.api_token, body.project_key, body.status_map)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JiraOut(site=row.site, email=row.email, project_key=row.project_key, status_map=row.status_map, connected=True)


@app.delete("/api/workspaces/{workspace_id}/jira")
def jira_disconnect(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    load_workspace(workspace_id, user, db)
    row = db.get(JiraConnection, workspace_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.post("/api/workspaces/{workspace_id}/jira/import")
def jira_import(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    load_workspace(workspace_id, user, db)
    connection = db.get(JiraConnection, workspace_id)
    if connection is None:
        raise HTTPException(status_code=400, detail="Connect Jira first")
    with httpx.Client(timeout=30) as client:
        issues = fetch_jira_issues(connection, client)
    counts = {"imported": 0, "updated": 0, "dropped": 0}
    for issue in issues:
        result = apply_jira_issue(db, workspace_id, issue)
        counts[result] = counts.get(result, 0) + 1
    return counts


@app.post("/api/webhooks/jira")
async def jira_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    payload = await request.json()
    workspace_id = request.query_params.get("workspace_id") or payload.get("workspace_id")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    issue = payload.get("issue") or payload
    if "key" not in issue:
        return {"ok": True, "ignored": True}
    enqueue_jira_event(workspace_id, _normalize_jira_webhook(issue))
    return {"ok": True}


def _normalize_jira_webhook(issue: dict) -> dict:
    fields = issue.get("fields") or {}
    properties = {item.get("key"): item.get("value") for item in issue.get("properties") or []}
    origin = properties.get("branchline.origin") or issue.get("origin")
    status = fields.get("status")
    if isinstance(status, dict):
        status = status.get("name")
    priority = fields.get("priority")
    if isinstance(priority, dict):
        priority = priority.get("name")
    assignee = fields.get("assignee")
    if isinstance(assignee, dict):
        assignee = assignee.get("displayName") or ""
    description = fields.get("description")
    if isinstance(description, dict):
        from app.services import _plain_description

        description = _plain_description(description)
    return {
        "key": issue["key"],
        "summary": fields.get("summary") or issue.get("summary") or "",
        "description": description or issue.get("description") or "",
        "status": status or issue.get("status") or "",
        "priority": priority or issue.get("priority") or "",
        "labels": fields.get("labels") or issue.get("labels") or [],
        "assignee": assignee or issue.get("assignee") or "",
        "origin": origin,
    }


def enqueue_jira_event(workspace_id: str, issue: dict) -> None:
    if settings.redis_url and not settings.sync_jobs:
        from app.worker import _celery

        _celery().send_task("branchline.jira_event", args=[workspace_id, issue])
        return
    from app.db import get_session

    db = get_session()
    try:
        apply_jira_issue(db, workspace_id, issue)
    finally:
        db.close()


@app.get("/api/workspaces/{workspace_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[LinkSuggestion]:
    load_workspace(workspace_id, user, db)
    return list(
        db.scalars(
            select(LinkSuggestion).where(LinkSuggestion.workspace_id == workspace_id).order_by(LinkSuggestion.created_at.desc())
        ).all()
    )


@app.post("/api/suggestions/{suggestion_id}/accept", response_model=SuggestionOut)
def accept_suggestion(suggestion_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> LinkSuggestion:
    return _decide(suggestion_id, True, user, db)


@app.post("/api/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
def reject_suggestion(suggestion_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> LinkSuggestion:
    return _decide(suggestion_id, False, user, db)


def _decide(suggestion_id: str, accepted: bool, user: User, db: Session) -> LinkSuggestion:
    suggestion = db.get(LinkSuggestion, suggestion_id)
    if suggestion is None or member_role(db, suggestion.workspace_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail="Suggestion is already decided")
    return decide_suggestion(db, suggestion, accepted)


@app.post("/api/cli/link", response_model=TaskOut)
def cli_link(body: LinkIn, request: Request, db: Session = Depends(get_db)) -> Task:
    token = request.headers.get("x-branchline-token", "")
    workspace = db.scalar(select(Workspace).where(Workspace.cli_token == token))
    if workspace is None:
        raise HTTPException(status_code=401, detail="Unknown token")
    task = None
    if body.task_number is not None:
        task = db.scalar(select(Task).where(Task.workspace_id == workspace.id, Task.number == body.task_number))
    if task is None:
        task = db.scalar(select(Task).where(Task.workspace_id == workspace.id, Task.git_branch == body.branch))
    if task is None:
        raise HTTPException(status_code=404, detail="No task for that branch")
    task.git_branch = body.branch
    task.state = "in_progress" if task.state == "todo" else task.state
    task.state_reason = f"Linked from branch `{body.branch}`"
    task.state_source = "git"
    db.commit()
    return task


@app.websocket("/api/ws/{workspace_id}")
async def workspace_socket(websocket: WebSocket, workspace_id: str) -> None:
    await websocket.accept()
    user_id = websocket.session.get("user_id") if "session" in websocket.scope else None
    if not user_id:
        await websocket.close(code=4401)
        return
    db = next(get_db())
    try:
        if member_role(db, workspace_id, user_id) is None:
            await websocket.close(code=4404)
            return
    finally:
        db.close()
    queue = hub.subscribe(workspace_id)
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        hub.unsubscribe(workspace_id, queue)
    finally:
        hub.unsubscribe(workspace_id, queue)

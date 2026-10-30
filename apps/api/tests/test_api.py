import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, create_all, get_engine, reset_db
from app.domain.jira_sync import content_hash, snapshot_fields
from app.main import app
from app.models import SyncAnchor, Task


@pytest.fixture
def client():
    get_settings.cache_clear()
    reset_db()
    engine = get_engine()
    Base.metadata.drop_all(engine)
    create_all()
    with TestClient(app) as test_client:
        yield test_client
    reset_db()


def sign(body: bytes) -> str:
    digest = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def login(client: TestClient, name: str = "ada") -> str:
    assert client.post("/api/auth/dev", json={"login": name}).status_code == 200
    workspace = client.post("/api/workspaces", json={"name": "Acme"}).json()
    return workspace["id"]


def test_board_works_without_jira(client: TestClient):
    workspace_id = login(client)
    created = client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Write the board", "description": "keys"})
    assert created.status_code == 200
    task = created.json()
    assert task["state"] == "todo"
    assert task["number"] == 1
    moved = client.patch(f"/api/tasks/{task['id']}", json={"state": "in_progress"})
    assert moved.json()["state_source"] == "manual"
    assert "Moved by ada" in moved.json()["state_reason"]
    exported = client.get(f"/api/workspaces/{workspace_id}/export")
    assert exported.json()["tasks"][0]["title"] == "Write the board"


def test_branch_then_push_does_not_duplicate(client: TestClient):
    workspace_id = login(client)
    repo = {"full_name": "acme/web", "default_branch": "main"}
    created = client.post(
        f"/api/workspaces/{workspace_id}/github/simulate",
        json={"event": "create", "payload": {"ref": "fix/login-bug", "ref_type": "branch", "repository": repo}},
    )
    assert created.status_code == 200
    assert created.json()["state"] == "in_progress"
    pushed = client.post(
        f"/api/workspaces/{workspace_id}/github/simulate",
        json={"event": "push", "payload": {"ref": "refs/heads/fix/login-bug", "repository": repo}},
    )
    assert pushed.status_code == 200
    assert pushed.json()["id"] == created.json()["id"]
    tasks = client.get(f"/api/workspaces/{workspace_id}/tasks").json()
    assert len(tasks) == 1
    inbox = client.get(f"/api/workspaces/{workspace_id}/inbox").json()
    assert inbox == []


def test_unmatched_push_lands_in_inbox(client: TestClient):
    workspace_id = login(client)
    repo = {"full_name": "acme/web", "default_branch": "main"}
    response = client.post(
        f"/api/workspaces/{workspace_id}/github/simulate",
        json={"event": "push", "payload": {"ref": "refs/heads/orphan", "repository": repo}},
    )
    assert response.status_code == 200
    assert response.json() is None
    inbox = client.get(f"/api/workspaces/{workspace_id}/inbox").json()
    assert len(inbox) == 1
    assert "orphan" in inbox[0]["summary"]


def test_pull_request_moves_the_card_and_suggests_a_link(client: TestClient):
    workspace_id = login(client)
    existing = client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Checkout tax rounding"}).json()
    repo = {"full_name": "acme/web", "default_branch": "main"}
    opened = client.post(
        f"/api/workspaces/{workspace_id}/github/simulate",
        json={
            "event": "pull_request",
            "payload": {
                "action": "opened",
                "repository": repo,
                "pull_request": {
                    "number": 12,
                    "title": "Fix checkout tax rounding",
                    "body": "tax line",
                    "html_url": "https://github.com/acme/web/pull/12",
                    "head": {"ref": "fix/tax"},
                    "merged": False,
                    "requested_reviewers": [{"login": "ada"}],
                },
            },
        },
    ).json()
    assert opened["state"] == "in_review"
    assert opened["pr_number"] == 12
    suggestions = client.get(f"/api/workspaces/{workspace_id}/suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["task_id"] == existing["id"]
    assert suggestions[0]["status"] == "pending"
    accepted = client.post(f"/api/suggestions/{suggestions[0]['id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    again = client.post(f"/api/suggestions/{suggestions[0]['id']}/accept")
    assert again.status_code == 400
    workspace = client.get("/api/workspaces").json()[0]
    assert workspace["ai_threshold"] != 0.4 or accepted.json()["confidence"] >= 0.4


def test_reject_stores_the_decision(client: TestClient):
    workspace_id = login(client)
    client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Checkout tax rounding"})
    repo = {"full_name": "acme/web", "default_branch": "main"}
    client.post(
        f"/api/workspaces/{workspace_id}/github/simulate",
        json={
            "event": "pull_request",
            "payload": {
                "action": "opened",
                "repository": repo,
                "pull_request": {
                    "number": 9,
                    "title": "Checkout tax",
                    "body": "",
                    "head": {"ref": "tax"},
                    "merged": False,
                    "requested_reviewers": [],
                },
            },
        },
    )
    suggestion = client.get(f"/api/workspaces/{workspace_id}/suggestions").json()[0]
    rejected = client.post(f"/api/suggestions/{suggestion['id']}/reject")
    assert rejected.json()["status"] == "rejected"


def test_webhook_verifies_signature_and_enqueues(client: TestClient):
    workspace_id = login(client)
    client.post(
        f"/api/workspaces/{workspace_id}/github",
        json={"installation_id": 77, "account_login": "acme", "repos": ["acme/web"]},
    )
    payload = {
        "installation": {"id": 77},
        "ref": "fix/webhook",
        "ref_type": "branch",
        "repository": {"full_name": "acme/web", "default_branch": "main"},
    }
    body = json.dumps(payload).encode()
    bad = client.post(
        "/api/webhooks/github",
        content=body,
        headers={"x-github-event": "create", "x-hub-signature-256": "sha256=nope", "content-type": "application/json"},
    )
    assert bad.status_code == 401
    good = client.post(
        "/api/webhooks/github",
        content=body,
        headers={"x-github-event": "create", "x-hub-signature-256": sign(body), "content-type": "application/json"},
    )
    assert good.status_code == 200
    tasks = client.get(f"/api/workspaces/{workspace_id}/tasks").json()
    assert tasks[0]["git_branch"] == "fix/webhook"


def test_delivery_standup_includes_stalled_work(client: TestClient):
    workspace_id = login(client)
    created = client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Quiet branch", "state": "in_progress"}).json()
    from app.db import get_session
    from datetime import datetime, timedelta, timezone

    db = get_session()
    task = db.get(Task, created["id"])
    task.last_push_at = datetime.now(timezone.utc) - timedelta(days=5)
    task.created_at = task.last_push_at
    db.commit()
    db.close()
    delivery = client.get(f"/api/workspaces/{workspace_id}/delivery").json()
    assert delivery["blocked"][0]["stall_reason"] == "No push for 3 days"
    text = client.get(f"/api/workspaces/{workspace_id}/standup").json()["text"]
    assert "Quiet branch" in text
    assert "Blocked" in text


def test_jira_echo_is_dropped_and_conflict_keeps_the_loser(client: TestClient):
    workspace_id = login(client)
    created = client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Login"}).json()
    status_map = {"todo": "To Do", "in_progress": "In Progress", "in_review": "In Review", "done": "Done"}
    connected = client.post(
        f"/api/workspaces/{workspace_id}/jira",
        json={
            "site": "https://acme.atlassian.net",
            "email": "ada@acme.dev",
            "api_token": "secret",
            "project_key": "APP",
            "status_map": status_map,
        },
    )
    assert connected.status_code == 200
    assert "secret" not in connected.text
    fields = snapshot_fields(title="Login", description="", state="todo", priority="none", labels=[], assignee="ada")
    digest = content_hash(fields)
    from app.db import get_session

    db = get_session()
    db.add(
        SyncAnchor(
            workspace_id=workspace_id,
            task_id=created["id"],
            jira_key="APP-1",
            content_hash=digest,
            origin=digest,
            sync_status="ok",
        )
    )
    db.commit()
    db.close()
    echo = client.post(
        f"/api/webhooks/jira?workspace_id={workspace_id}",
        json={"key": "APP-1", "summary": "Login", "description": "", "status": "To Do", "priority": "Lowest", "labels": [], "assignee": "ada", "origin": digest},
    )
    assert echo.json()["ok"] is True
    remote = client.post(
        f"/api/webhooks/jira?workspace_id={workspace_id}",
        json={"key": "APP-1", "summary": "Login timeout", "description": "", "status": "In Progress", "priority": "High", "labels": ["bug"], "assignee": "ada", "origin": digest},
    )
    assert remote.status_code == 200
    task = client.get(f"/api/workspaces/{workspace_id}/tasks").json()[0]
    assert task["title"] == "Login timeout"
    assert task["state"] == "in_progress"
    db = get_session()
    anchor = db.query(SyncAnchor).filter_by(jira_key="APP-1").one()
    assert anchor.sync_status == "conflict"
    assert "Login" in anchor.conflict_resolution
    db.close()


def test_disconnect_leaves_tasks(client: TestClient):
    workspace_id = login(client)
    client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "Stays"})
    client.post(
        f"/api/workspaces/{workspace_id}/jira",
        json={
            "site": "https://acme.atlassian.net",
            "email": "ada@acme.dev",
            "api_token": "secret",
            "project_key": "APP",
            "status_map": {"todo": "To Do", "in_progress": "In Progress", "in_review": "In Review", "done": "Done"},
        },
    )
    assert client.delete(f"/api/workspaces/{workspace_id}/jira").status_code == 200
    assert client.get(f"/api/workspaces/{workspace_id}/tasks").json()[0]["title"] == "Stays"
    assert client.get(f"/api/workspaces/{workspace_id}/jira").json()["connected"] is False


def test_cli_links_a_branch(client: TestClient):
    workspace_id = login(client)
    token = client.get("/api/workspaces").json()[0]["cli_token"]
    task = client.post(f"/api/workspaces/{workspace_id}/tasks", json={"title": "CLI"}).json()
    linked = client.post(
        "/api/cli/link",
        json={"branch": "fix/cli", "task_number": task["number"]},
        headers={"x-branchline-token": token},
    )
    assert linked.status_code == 200
    assert linked.json()["git_branch"] == "fix/cli"

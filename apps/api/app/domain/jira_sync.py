import hashlib
import json
from collections.abc import Callable

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

ORIGIN_PROPERTY = "branchline.origin"

PRIORITY_TO_JIRA = {
    "urgent": "Highest",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "none": "Lowest",
}
JIRA_TO_PRIORITY = {value: key for key, value in PRIORITY_TO_JIRA.items()}

STATES = ("todo", "in_progress", "in_review", "done")


class JiraRateLimit(Exception):
    pass


def content_hash(fields: dict) -> str:
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def should_drop_inbound(stored_hash: str, incoming_hash: str, origin: str | None) -> bool:
    """Drop the webhook when it is the echo of our own write."""
    return bool(origin) and origin == stored_hash and incoming_hash == stored_hash


def snapshot_fields(
    *,
    title: str,
    description: str,
    state: str,
    priority: str,
    labels: list[str],
    assignee: str,
) -> dict:
    return {
        "title": title,
        "description": description,
        "state": state,
        "priority": priority,
        "labels": sorted(labels),
        "assignee": assignee,
    }


def outbound_issue(fields: dict, status_map: dict[str, str], task_id: str) -> dict:
    return {
        "summary": fields["title"],
        "description": fields["description"],
        "status": status_map[fields["state"]],
        "priority": PRIORITY_TO_JIRA.get(fields["priority"], "Medium"),
        "labels": fields["labels"],
        "assignee": fields["assignee"],
        ORIGIN_PROPERTY: content_hash(fields),
        "branchline.task_id": task_id,
    }


def inbound_fields(issue: dict, status_map: dict[str, str]) -> dict:
    reverse = {jira: local for local, jira in status_map.items()}
    status_name = issue.get("status") or ""
    return snapshot_fields(
        title=issue.get("summary") or "",
        description=issue.get("description") or "",
        state=reverse.get(status_name, "todo"),
        priority=JIRA_TO_PRIORITY.get(issue.get("priority") or "", "none"),
        labels=list(issue.get("labels") or []),
        assignee=issue.get("assignee") or "",
    )


def validate_status_map(status_map: dict) -> None:
    missing = [state for state in STATES if not status_map.get(state)]
    if missing:
        raise ValueError("Status map needs " + ", ".join(missing))


def call_with_backoff(fn: Callable[[], object], wait_min: float = 1) -> object:
    @retry(
        retry=retry_if_exception_type(JiraRateLimit),
        wait=wait_exponential(multiplier=wait_min, min=wait_min, max=max(wait_min * 8, wait_min)),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def attempt():
        return fn()

    return attempt()

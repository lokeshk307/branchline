from dataclasses import dataclass
from datetime import datetime, timedelta


REVIEW_STALL = timedelta(days=2)
PROGRESS_STALL = timedelta(days=3)


@dataclass
class DeliveryTask:
    number: int
    title: str
    state: str
    owner_label: str
    checks_status: str
    last_push_at: datetime | None
    last_review_at: datetime | None
    created_at: datetime
    updated_at: datetime
    pr_number: int | None


def stall_reason(task: DeliveryTask, now: datetime) -> str | None:
    if task.checks_status == "failing":
        return "Checks failing"
    if task.state == "in_review" and task.pr_number is not None:
        reviewed = task.last_review_at or task.created_at
        if now - reviewed >= REVIEW_STALL:
            return "No review for 2 days"
    if task.state == "in_progress":
        pushed = task.last_push_at or task.created_at
        if now - pushed >= PROGRESS_STALL:
            return "No push for 3 days"
    return None


def standup_text(groups: dict[str, list[tuple[DeliveryTask, str | None]]], when: str) -> str:
    lines = [f"Branchline standup — {when}", ""]
    labels = {
        "in_progress": "In progress",
        "in_review": "In review",
        "done": "Done this week",
        "blocked": "Blocked",
    }
    for key in ("in_progress", "in_review", "done", "blocked"):
        rows = groups.get(key) or []
        lines.append(labels[key])
        if not rows:
            lines.append("- Nothing here")
        for task, reason in rows:
            owner = task.owner_label or "Unassigned"
            extra = f" — {reason}" if reason else ""
            lines.append(f"- BL-{task.number} {task.title} — {owner}{extra}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

from dataclasses import dataclass, field


@dataclass
class Intent:
    repo: str
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    title: str | None = None
    description: str | None = None
    state: str | None = None
    reason: str = ""
    checks_status: str | None = None
    changed_files: list[str] = field(default_factory=list)
    reviewer_logins: list[str] = field(default_factory=list)
    create_if_missing: bool = False
    inbox_if_missing: bool = False
    inbox_summary: str = ""
    touch: str | None = None


def _repo(payload: dict) -> str:
    return payload["repository"]["full_name"]


def _default_branch(payload: dict) -> str:
    return payload.get("repository", {}).get("default_branch") or ""


def interpret(event: str, payload: dict) -> Intent | None:
    """Conservative GitHub rules. A push never creates a task."""
    if event == "create" and payload.get("ref_type") == "branch":
        ref = payload.get("ref") or ""
        if not ref or ref == _default_branch(payload):
            return None
        return Intent(
            repo=_repo(payload),
            branch=ref,
            title=ref,
            state="in_progress",
            reason=f"Created from branch `{ref}`",
            create_if_missing=True,
            touch="push",
        )

    if event == "push":
        ref = payload.get("ref") or ""
        if payload.get("deleted") or not ref.startswith("refs/heads/"):
            return None
        branch = ref.removeprefix("refs/heads/")
        if branch == _default_branch(payload):
            return None
        return Intent(
            repo=_repo(payload),
            branch=branch,
            reason=f"Pushed to `{branch}`",
            create_if_missing=False,
            inbox_if_missing=True,
            inbox_summary=f"Push on `{branch}` did not match a task",
            touch="push",
        )

    if event == "pull_request":
        return _pull_request(payload)

    if event == "pull_request_review" and payload.get("action") == "submitted":
        pr = payload["pull_request"]
        number = pr["number"]
        return Intent(
            repo=_repo(payload),
            branch=pr["head"]["ref"],
            pr_number=number,
            pr_url=pr.get("html_url"),
            reason=f"Review on pull request {number}",
            create_if_missing=False,
            inbox_if_missing=True,
            inbox_summary=f"Review on pull request {number} did not match a task",
            touch="review",
        )

    if event == "check_run" and payload.get("action") == "completed":
        return _check(payload, payload["check_run"])

    if event == "check_suite" and payload.get("action") == "completed":
        return _check(payload, payload["check_suite"])

    return None


def _pull_request(payload: dict) -> Intent | None:
    action = payload.get("action")
    pr = payload["pull_request"]
    number = pr["number"]
    branch = pr["head"]["ref"]
    reviewers = [
        user.get("login")
        for user in (pr.get("requested_reviewers") or [])
        if user.get("login")
    ]
    base = Intent(
        repo=_repo(payload),
        branch=branch,
        pr_number=number,
        pr_url=pr.get("html_url"),
        title=pr.get("title"),
        description=pr.get("body") or "",
        reviewer_logins=reviewers,
        changed_files=list(pr.get("changed_files_list") or []),
    )
    if action in {"opened", "reopened", "ready_for_review"}:
        base.state = "in_review"
        base.reason = f"In Review, pull request {number}"
        base.create_if_missing = True
        base.touch = "push"
        return base
    if action == "synchronize":
        base.reason = f"Updated pull request {number}"
        base.create_if_missing = False
        base.inbox_if_missing = True
        base.inbox_summary = f"Update on pull request {number} did not match a task"
        base.touch = "push"
        return base
    if action == "closed" and pr.get("merged"):
        base.state = "done"
        base.reason = f"Merged pull request {number}"
        base.create_if_missing = False
        base.inbox_if_missing = True
        base.inbox_summary = f"Merged pull request {number} did not match a task"
        return base
    if action == "closed":
        base.reason = f"Closed pull request {number} without merging"
        base.create_if_missing = False
        base.inbox_if_missing = True
        base.inbox_summary = base.reason
        return base
    return None


def _check(payload: dict, run: dict) -> Intent | None:
    conclusion = run.get("conclusion")
    if conclusion in {"failure", "timed_out", "cancelled"}:
        status = "failing"
        reason = "Checks failed"
        state = "in_review"
    elif conclusion == "success":
        status = "passing"
        reason = "Checks passed"
        state = None
    else:
        return None
    prs = run.get("pull_requests") or []
    if not prs:
        return Intent(
            repo=_repo(payload),
            reason=reason,
            checks_status=status,
            create_if_missing=False,
            inbox_if_missing=True,
            inbox_summary=f"{reason}, and no pull request was attached",
        )
    pr = prs[0]
    return Intent(
        repo=_repo(payload),
        pr_number=pr.get("number"),
        branch=(pr.get("head") or {}).get("ref"),
        state=state,
        reason=reason,
        checks_status=status,
        create_if_missing=False,
        inbox_if_missing=True,
        inbox_summary=f"{reason} for pull request {pr.get('number')}, with no matching task",
    )


def intents_from_import(open_prs: list[dict], merged_prs: list[dict]) -> list[Intent]:
    intents: list[Intent] = []
    for pr in open_prs:
        reviewers = [
            user.get("login")
            for user in (pr.get("requested_reviewers") or [])
            if user.get("login")
        ]
        intents.append(
            Intent(
                repo=pr["repo"],
                branch=pr["head"],
                pr_number=pr["number"],
                pr_url=pr.get("url"),
                title=pr["title"],
                description=pr.get("body") or "",
                state="in_review",
                reason=f"In Review, pull request {pr['number']}",
                changed_files=list(pr.get("files") or []),
                reviewer_logins=reviewers,
                create_if_missing=True,
                touch="push",
            )
        )
    for pr in merged_prs:
        intents.append(
            Intent(
                repo=pr["repo"],
                branch=pr.get("head"),
                pr_number=pr["number"],
                pr_url=pr.get("url"),
                title=pr["title"],
                description=pr.get("body") or "",
                state="done",
                reason=f"Merged pull request {pr['number']}",
                changed_files=list(pr.get("files") or []),
                create_if_missing=True,
            )
        )
    return intents

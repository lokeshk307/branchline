from datetime import datetime, timedelta, timezone

from app.domain.github_rules import interpret


def payload_repo(default="main"):
    return {"full_name": "acme/web", "default_branch": default}


def test_branch_creates_task():
    intent = interpret("create", {"ref": "fix/login-bug", "ref_type": "branch", "repository": payload_repo()})
    assert intent is not None
    assert intent.create_if_missing is True
    assert intent.state == "in_progress"
    assert "fix/login-bug" in intent.reason


def test_default_branch_is_ignored():
    assert interpret("create", {"ref": "main", "ref_type": "branch", "repository": payload_repo()}) is None


def test_push_does_not_create():
    intent = interpret(
        "push",
        {"ref": "refs/heads/fix/login-bug", "repository": payload_repo()},
    )
    assert intent is not None
    assert intent.create_if_missing is False
    assert intent.inbox_if_missing is True


def test_pull_request_opens_review():
    intent = interpret(
        "pull_request",
        {
            "action": "opened",
            "repository": payload_repo(),
            "pull_request": {
                "number": 142,
                "title": "Fix login",
                "html_url": "https://github.com/acme/web/pull/142",
                "head": {"ref": "fix/login-bug"},
                "merged": False,
                "requested_reviewers": [],
            },
        },
    )
    assert intent is not None
    assert intent.state == "in_review"
    assert intent.reason == "In Review, pull request 142"
    assert intent.create_if_missing is True


def test_merge_marks_done():
    intent = interpret(
        "pull_request",
        {
            "action": "closed",
            "repository": payload_repo(),
            "pull_request": {
                "number": 142,
                "title": "Fix login",
                "head": {"ref": "fix/login-bug"},
                "merged": True,
                "requested_reviewers": [],
            },
        },
    )
    assert intent is not None
    assert intent.state == "done"


def test_failing_checks_stay_attached():
    intent = interpret(
        "check_run",
        {
            "action": "completed",
            "repository": payload_repo(),
            "check_run": {
                "conclusion": "failure",
                "pull_requests": [{"number": 142, "head": {"ref": "fix/login-bug"}}],
            },
        },
    )
    assert intent is not None
    assert intent.checks_status == "failing"
    assert intent.state == "in_review"
    assert intent.create_if_missing is False

from app.domain.jira_sync import (
    JiraRateLimit,
    call_with_backoff,
    content_hash,
    inbound_fields,
    outbound_issue,
    should_drop_inbound,
    snapshot_fields,
    validate_status_map,
)


STATUS = {
    "todo": "To Do",
    "in_progress": "In Progress",
    "in_review": "In Review",
    "done": "Done",
}


def test_echo_is_dropped():
    fields = snapshot_fields(
        title="Login",
        description="",
        state="in_progress",
        priority="none",
        labels=["api"],
        assignee="ada",
    )
    digest = content_hash(fields)
    assert should_drop_inbound(digest, digest, digest) is True


def test_external_edit_is_kept():
    fields = snapshot_fields(
        title="Login",
        description="",
        state="in_progress",
        priority="none",
        labels=[],
        assignee="",
    )
    digest = content_hash(fields)
    changed = dict(fields)
    changed["title"] = "Login timeout"
    assert should_drop_inbound(digest, content_hash(changed), digest) is False
    assert should_drop_inbound(digest, digest, None) is False


def test_status_map_is_required():
    try:
        validate_status_map({"todo": "To Do"})
    except ValueError as exc:
        assert "in_progress" in str(exc)
    else:
        raise AssertionError("expected a status map error")


def test_round_trip_fields():
    fields = snapshot_fields(
        title="Login",
        description="steps",
        state="in_review",
        priority="high",
        labels=["bug"],
        assignee="ada",
    )
    issue = {
        "summary": "Login",
        "description": "steps",
        "status": "In Review",
        "priority": "High",
        "labels": ["bug"],
        "assignee": "ada",
    }
    assert inbound_fields(issue, STATUS) == fields
    outbound = outbound_issue(fields, STATUS, "task-1")
    assert outbound["status"] == "In Review"
    assert outbound["branchline.origin"] == content_hash(fields)
    assert outbound["branchline.task_id"] == "task-1"


def test_rate_limit_retries():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise JiraRateLimit()
        return "ok"

    assert call_with_backoff(flaky, wait_min=0.01) == "ok"
    assert calls["n"] == 3

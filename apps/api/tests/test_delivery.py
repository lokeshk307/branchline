from datetime import datetime, timedelta, timezone

from app.domain.delivery import DeliveryTask, stall_reason, standup_text


def task(**kwargs) -> DeliveryTask:
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    base = dict(
        number=1,
        title="Login",
        state="in_progress",
        owner_label="ada",
        checks_status="none",
        last_push_at=now,
        last_review_at=None,
        created_at=now,
        updated_at=now,
        pr_number=None,
    )
    base.update(kwargs)
    return DeliveryTask(**base)


def test_failing_checks_are_blocked():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert stall_reason(task(checks_status="failing", state="in_review", pr_number=4), now) == "Checks failing"


def test_quiet_review():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    reason = stall_reason(
        task(state="in_review", pr_number=4, last_review_at=now - timedelta(days=3), checks_status="passing"),
        now,
    )
    assert reason == "No review for 2 days"


def test_quiet_progress():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    reason = stall_reason(task(last_push_at=now - timedelta(days=4)), now)
    assert reason == "No push for 3 days"


def test_fresh_work_is_not_stalled():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert stall_reason(task(), now) is None


def test_standup_lists_sections():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    text = standup_text(
        {"in_progress": [(task(), "No push for 3 days")], "in_review": [], "done": [], "blocked": []},
        "9 Oct",
    )
    assert "Branchline standup — 9 Oct" in text
    assert "BL-1 Login — ada — No push for 3 days" in text
    assert "Nothing here" in text

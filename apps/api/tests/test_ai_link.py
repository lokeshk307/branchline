from app.domain.ai_link import TaskRef, heuristic_link, next_threshold


def test_suggestion_names_the_shared_words():
    decision = heuristic_link(
        "Fix checkout tax rounding",
        "The tax line is wrong",
        [TaskRef(id="a", number=4, title="Checkout tax"), TaskRef(id="b", number=5, title="Readme")],
    )
    assert decision is not None
    assert decision.task_id == "a"
    assert decision.confidence > 0
    assert "BL-4" in decision.reason


def test_unrelated_pr_has_no_suggestion():
    assert heuristic_link("Rename the icon", "", [TaskRef(id="a", number=1, title="Database backup")]) is None


def test_reject_raises_the_threshold():
    assert next_threshold(0.4, 0.62, accepted=False) == 0.43


def test_accept_below_threshold_lowers_it():
    assert next_threshold(0.4, 0.2, accepted=True) == 0.38

import re
from dataclasses import dataclass


@dataclass
class TaskRef:
    id: str
    number: int
    title: str


@dataclass
class LinkDecision:
    task_id: str | None
    confidence: float
    reason: str


_words = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set[str]:
    return {word for word in _words.findall(text.lower()) if len(word) > 2}


def heuristic_link(pr_title: str, pr_body: str, tasks: list[TaskRef]) -> LinkDecision | None:
    pr_tokens = tokens(f"{pr_title} {pr_body}")
    if not pr_tokens or not tasks:
        return None
    best: tuple[float, TaskRef] | None = None
    for task in tasks:
        shared = pr_tokens & tokens(task.title)
        if not shared:
            continue
        score = len(shared) / len(pr_tokens)
        if best is None or score > best[0]:
            best = (score, task)
    if best is None:
        return None
    score, task = best
    shared = sorted(pr_tokens & tokens(task.title))
    return LinkDecision(
        task_id=task.id,
        confidence=round(min(score, 0.99), 2),
        reason=f"Shares {', '.join(shared)} with BL-{task.number}",
    )


def next_threshold(current: float, confidence: float, accepted: bool) -> float:
    if accepted and confidence < current:
        return max(0.15, round(current - 0.02, 2))
    if not accepted and confidence >= current:
        return min(0.8, round(current + 0.03, 2))
    return current

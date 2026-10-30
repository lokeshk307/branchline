from typing import Protocol

from app.domain.github_rules import Intent, interpret


class SourceAdapter(Protocol):
    name: str

    def parse(self, event: str, payload: dict) -> Intent | None: ...


class GitHubAdapter:
    """GitHub is the first source. GitLab can implement the same protocol later."""

    name = "github"

    def parse(self, event: str, payload: dict) -> Intent | None:
        return interpret(event, payload)


github_adapter = GitHubAdapter()

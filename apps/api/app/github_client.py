import time

import httpx
import jwt

from app.config import get_settings


def app_token() -> str:
    settings = get_settings()
    if not settings.github_app_id or not settings.github_private_key:
        raise RuntimeError("GitHub App credentials are not configured")
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id},
        settings.github_private_key.replace("\\n", "\n"),
        algorithm="RS256",
    )


def installation_token(installation_id: int, client: httpx.Client | None = None) -> str:
    owns = client is None
    client = client or httpx.Client(timeout=20)
    try:
        response = client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_token()}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        return response.json()["token"]
    finally:
        if owns:
            client.close()


def list_repo_pulls(token: str, repo: str, client: httpx.Client) -> tuple[list[dict], list[dict]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    open_response = client.get(
        f"https://api.github.com/repos/{repo}/pulls",
        params={"state": "open", "per_page": 50},
        headers=headers,
    )
    open_response.raise_for_status()
    closed = client.get(
        f"https://api.github.com/repos/{repo}/pulls",
        params={"state": "closed", "per_page": 50, "sort": "updated", "direction": "desc"},
        headers=headers,
    )
    closed.raise_for_status()
    open_prs = [_compact(repo, item) for item in open_response.json()]
    merged = []
    for item in closed.json():
        if not item.get("merged_at"):
            continue
        merged.append(_compact(repo, item))
    return open_prs, merged[:30]


def _compact(repo: str, item: dict) -> dict:
    return {
        "repo": repo,
        "number": item["number"],
        "title": item["title"],
        "body": item.get("body") or "",
        "head": item["head"]["ref"],
        "url": item.get("html_url"),
        "files": [],
        "requested_reviewers": item.get("requested_reviewers") or [],
        "merged_at": item.get("merged_at"),
    }

from app.config import get_settings
from app.db import get_session


def _celery():
    from celery import Celery

    settings = get_settings()
    app = Celery("branchline", broker=settings.redis_url, backend=settings.redis_url)
    return app


def _async_enabled() -> bool:
    settings = get_settings()
    return bool(settings.redis_url) and not settings.sync_jobs


def enqueue_github(workspace_id: str, event: str, payload: dict) -> None:
    if _async_enabled():
        _celery().send_task("branchline.process_github", args=[workspace_id, event, payload])
        return
    from app.services import apply_github_event

    db = get_session()
    try:
        apply_github_event(db, workspace_id, event, payload)
    finally:
        db.close()


def enqueue_jira_push(workspace_id: str, task_id: str) -> None:
    if _async_enabled():
        _celery().send_task("branchline.push_jira", args=[workspace_id, task_id])
        return
    from app.services import push_task_to_jira

    db = get_session()
    try:
        push_task_to_jira(db, workspace_id, task_id)
    finally:
        db.close()


def register(celery_app) -> None:
    @celery_app.task(name="branchline.process_github")
    def process_github(workspace_id: str, event: str, payload: dict) -> None:
        from app.services import apply_github_event

        db = get_session()
        try:
            apply_github_event(db, workspace_id, event, payload)
        finally:
            db.close()

    @celery_app.task(name="branchline.push_jira")
    def push_jira(workspace_id: str, task_id: str) -> None:
        from app.services import push_task_to_jira

        db = get_session()
        try:
            push_task_to_jira(db, workspace_id, task_id)
        finally:
            db.close()

# Branchline

The board stays true to the work. Sign in, connect a repository, and tasks move when branches and pull requests move.

## Run it

API (Python 3.12):

```bash
cd apps/api
python3.12 -m pip install fastapi 'uvicorn' 'sqlalchemy>=2' alembic pydantic-settings authlib httpx itsdangerous celery redis tenacity sentry-sdk typer pyjwt
PYTHONPATH=. python3.12 -m uvicorn app.main:app --reload
```

Or, with uv: `uv sync && uv run uvicorn app.main:app --reload`.

Web:

```bash
cd apps/web
pnpm install
pnpm dev
```

Open http://localhost:5173. Without GitHub OAuth configured, use the name field on the sign-in screen. Postgres is selected with `DATABASE_URL`. The default local database is SQLite so the app starts with no extra services. Set `REDIS_URL` and `SYNC_JOBS=false` when you want Celery to own webhook work.

## GitHub and Jira

Set `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_PRIVATE_KEY`, and `GITHUB_WEBHOOK_SECRET` for a live installation. Until those are set, onboarding can replay a branch event through the same rules the webhook uses.

Jira is optional, in Settings. The fixed field set is summary, description, status, assignee, priority, and labels. Outbound writes set the `branchline.origin` issue property so the inbound webhook can drop its own echo.

## Tests

```bash
cd apps/api
PYTHONPATH=. python3.12 -m pytest -q
```

`branchline link` reads the current git branch and calls `POST /api/cli/link` with the workspace token from Settings.

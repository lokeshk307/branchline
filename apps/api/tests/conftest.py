import os

os.environ["DATABASE_URL"] = "sqlite:////tmp/branchline-pytest.db"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SYNC_JOBS"] = "true"
os.environ["GITHUB_WEBHOOK_SECRET"] = "shh"
os.environ["ALLOW_SIMULATE"] = "true"
os.environ["SENTRY_DSN"] = ""
os.environ["REDIS_URL"] = ""

from app.config import get_settings

get_settings.cache_clear()

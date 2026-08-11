"""Central config: paths, non-secret settings, and secret access.

PROJECT_ROOT is derived from this file's own location so behavior is identical
regardless of the process's current working directory (important once this runs
under Windows Task Scheduler, whose default cwd is unrelated to this project).
"""

from pathlib import Path

from dotenv import load_dotenv
import os

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "super_news.db"
TOKEN_STORE_PATH = DATA_DIR / "kakao_token.json"
ENV_PATH = PROJECT_ROOT / ".env"

TIMEZONE = "Asia/Seoul"
HTTP_TIMEOUT_SECONDS = 10
ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300

_dotenv_loaded = False


def _ensure_dotenv_loaded():
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv(ENV_PATH)
        _dotenv_loaded = True


class MissingSecretError(RuntimeError):
    """Raised when a required environment variable is not set.

    Never includes the (absent) value in its message — there is nothing to leak,
    but this keeps the invariant obvious at the call site.
    """


def get_required_env(name):
    _ensure_dotenv_loaded()
    value = os.environ.get(name)
    if not value:
        raise MissingSecretError(
            f"Required environment variable '{name}' is not set. "
            f"Copy super-news/.env.example to super-news/.env and fill it in."
        )
    return value


def get_optional_env(name, default=None):
    _ensure_dotenv_loaded()
    return os.environ.get(name, default)


def ensure_runtime_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

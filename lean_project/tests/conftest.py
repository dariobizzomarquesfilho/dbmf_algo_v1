import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "implied_erp"))


import pytest


@pytest.fixture
def config_env():
    """Make ``config`` importable by providing a minimal .env (SEC_USER required).

    config.py raises at import time unless a .env with SEC_USER exists. This
    fixture writes a temporary one (backing up any real .env) and removes it on
    teardown so the repo is left untouched.
    """
    env_path = (
        Path(__file__).resolve().parent.parent.parent / "config" / ".env"
    )
    backup = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    env_path.write_text("SEC_USER=Test User test@example.com\n", encoding="utf-8")
    yield
    if backup is None:
        try:
            env_path.unlink()
        except FileNotFoundError:
            pass
    else:
        env_path.write_text(backup, encoding="utf-8")


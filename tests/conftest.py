"""Keep regression fixtures out of user-facing episode records."""
from functools import wraps
import pytest


@pytest.fixture(autouse=True)
def isolate_default_episode_records(monkeypatch, tmp_path):
    from sc2bench_env import Environment
    original = Environment.__init__

    @wraps(original)
    def isolated(self, *args, **kwargs):
        # Explicit recording roots are untouched. Non-pytest callers are unaffected.
        if kwargs.get("record_dir") is None:
            kwargs["record_dir"] = tmp_path / "records"
        original(self, *args, **kwargs)

    monkeypatch.setattr(Environment, "__init__", isolated)

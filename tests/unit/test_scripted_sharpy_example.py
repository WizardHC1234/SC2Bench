"""The real-backend example submits the current public action contract."""

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend


def test_scripted_sharpy_example_has_no_schema_rejections(tmp_path):
    from tests.helpers.scripted_episode import run_episode

    env = Environment(FakeBackend(mineral_income_per_second=25), record_dir=tmp_path)
    try:
        run_episode(env)
    finally:
        env.close()
    trajectory = env.trajectory()
    assert trajectory["summary"]["rejected_count"] == 0
    assert trajectory["steps"]

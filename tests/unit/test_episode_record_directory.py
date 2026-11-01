"""One reset starts one episode directory, not one per model decision."""
from tests.helpers.drive import drive
import json
from sc2bench_env import Environment


def test_many_decisions_and_rejections_share_one_episode_directory(tmp_path):
    root = tmp_path / "sessions"
    env = Environment(record_dir=root)
    try:
        env.reset()
        episode = env.record_path
        for _ in range(3):
            drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
        drive(env, [{"name": "retreat", "arguments": {'group': "group_0"}}, {"name": "advance", "arguments": {'seconds': 5}}])
        assert env.record_path == episode
        assert list(root.iterdir()) == [episode]
        assert sum(row["type"] == "step" for row in env.trajectory()["steps"]) == 4
    finally:
        env.close()
    assert list(root.iterdir()) == [episode]
    assert {path.name for path in episode.iterdir()} == {"episode.txt", "session.json", "metrics.json", "log.txt", "evaluation.jsonl.gz"}


def test_default_test_records_are_isolated(tmp_path):
    env = Environment()
    try:
        env.reset()
        assert env.record_path.parent == tmp_path / "records"
    finally:
        env.close()


def test_readable_names_and_same_second_collision_preserve_individual_games(tmp_path, monkeypatch):
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.recording import trajectory
    original_name = trajectory.episode_folder_name
    config = EpisodeConfig().to_dict()
    name = original_name(config)
    import re
    from datetime import datetime
    assert re.fullmatch(r"\d{6}_\d{6}_TvT_easy_KairosJunctionLE", name)
    stamp = datetime.strptime("_".join(name.split("_")[:2]), "%y%m%d_%H%M%S")
    assert abs((datetime.now() - stamp).total_seconds()) < 2
    monkeypatch.setattr(trajectory, "episode_folder_name", lambda _: name)
    occupied = tmp_path / name
    occupied.mkdir()
    marker = occupied / "existing.txt"
    marker.write_text("preserve", encoding="utf-8")
    first = trajectory.TrajectoryRecorder(episode_id="internal-first", config=config)
    second = trajectory.TrajectoryRecorder(episode_id="internal-second", config=config)
    assert first.start(tmp_path, prompt="", backend="fake").name == name + "_2"
    assert second.start(tmp_path, prompt="", backend="fake").name == name + "_3"
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert "internal-first" in (first.directory / "session.json").read_text(encoding="utf-8")


def test_folder_metadata_cannot_escape_record_root(tmp_path):
    from sc2bench_env.recording.trajectory import TrajectoryRecorder
    recorder = TrajectoryRecorder(config={"opponent": "../enemy:bot", "map_name": "../../map\\name"})
    directory = recorder.start(tmp_path, prompt="", backend="fake")
    assert directory.parent == tmp_path
    assert ":" not in directory.name

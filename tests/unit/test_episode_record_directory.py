"""One reset starts one episode directory, not one per model decision."""
import json
from sc2bench_env import Environment


def test_many_decisions_and_rejections_share_one_episode_directory(tmp_path):
    root = tmp_path / "sessions"
    env = Environment(record_dir=root)
    try:
        env.reset()
        episode = env.record_path
        for _ in range(3):
            env.step([{"action": "wait"}])
        env.step([{"action": "retreat", "group": "group_0"}, {"action": "wait"}])
        assert env.record_path == episode
        assert list(root.iterdir()) == [episode]
        from sc2bench_env.recording.reader import read_episode
        assert len(read_episode(episode)["interactions"]) == 4
    finally:
        env.close()
    assert list(root.iterdir()) == [episode]
    assert {path.name for path in episode.iterdir()} == {"episode.txt", "interactions.jsonl"}


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
    original_name = trajectory._episode_folder_name
    config = EpisodeConfig().to_dict()
    name = original_name(config)
    import re
    assert re.fullmatch(r"\d{6}_\d{6}_TvT_easy_KairosJunctionLE", name)
    monkeypatch.setattr(trajectory, "_episode_folder_name", lambda _: name)
    occupied = tmp_path / name
    occupied.mkdir()
    marker = occupied / "existing.txt"
    marker.write_text("preserve", encoding="utf-8")
    first = trajectory.TrajectoryRecorder(episode_id="internal-first", config=config)
    second = trajectory.TrajectoryRecorder(episode_id="internal-second", config=config)
    assert first.start(tmp_path, prompt="", backend="fake").name == name + "_2"
    assert second.start(tmp_path, prompt="", backend="fake").name == name + "_3"
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert "internal-first" in (first.directory / "interactions.jsonl").read_text(encoding="utf-8")


def test_folder_metadata_cannot_escape_record_root(tmp_path):
    from sc2bench_env.recording.trajectory import TrajectoryRecorder
    recorder = TrajectoryRecorder(config={"opponent": "../enemy:bot", "map_name": "../../map\\name"})
    directory = recorder.start(tmp_path, prompt="", backend="fake")
    assert directory.parent == tmp_path
    assert ":" not in directory.name

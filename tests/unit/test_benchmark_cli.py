"""Platform entry point and offline fact recomputation, without SC2/model calls."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.terran_baseline import DEFAULT_SUITE
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.benchmark.__main__ import main


def _short_suite():
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["repetitions"] = 1
    data["episode_defaults"]["game_time_limit_seconds"] = 1
    return BenchmarkSuite.from_dict(data)


def _agent():
    from tests.helpers.drive import scripted_advance
    return lambda request: scripted_advance(request, seconds=1)


def _batch(tmp_path):
    return BenchmarkRunner(backend_factory=FakeBackend, record_dir=tmp_path / "records").run(
        _short_suite(), _agent)


def _index(batch, directory):
    path = Path(directory) / "batch.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    return path


def test_cli_loads_external_factory_and_evaluates_read_only(tmp_path, monkeypatch, capsys):
    created = []

    def factory():
        created.append(object())
        return _agent()

    monkeypatch.setitem(sys.modules, "test_external_agent", SimpleNamespace(create_agent=factory))
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_short_suite().to_dict()), encoding="utf-8")
    assert main(["run", "--suite", str(suite_path), "--agent", "test_external_agent:create_agent",
                 "--backend", "fake", "--record-dir", str(tmp_path / "records")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(created) == 1 and result["aggregate"]["tie"] == 1
    assert not (tmp_path / "records" / "runs").exists()
    path = _index(_batch(tmp_path / "offline"), tmp_path)
    before = path.read_bytes()
    assert main(["evaluate", str(path)]) == 0
    recomputed = json.loads(capsys.readouterr().out)
    assert recomputed["aggregate"]["tie"] == 1
    assert path.read_bytes() == before
    assert len(created) == 1  # evaluation never creates another Agent


def test_offline_evaluation_ignores_cached_outcomes_and_aggregates(tmp_path):
    batch = _batch(tmp_path)
    path = _index(batch, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["episodes"][0]["outcome"] = "victory"
    saved["aggregate"]["victory"] = 999
    path.write_text(json.dumps(saved), encoding="utf-8")
    before = path.read_bytes()
    result = Evaluator.evaluate_batch(path)
    assert result["aggregate"]["victory"] == 0 and result["aggregate"]["tie"] == 1
    assert result["planned_episode_count"] == result["indexed_episode_count"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["truncated", "mismatched_id", "missing_end"])
def test_unreadable_mismatched_and_incomplete_records_do_not_become_defeats(tmp_path, damage):
    batch = _batch(tmp_path)
    path = _index(batch, tmp_path)
    first = batch["episodes"][0]
    record = (Path(first["record_directory"]) / "episode.txt").resolve()
    text = record.read_text(encoding="utf-8")
    if damage == "truncated":
        record.write_text("{broken", encoding="utf-8")
    elif damage == "mismatched_id":
        record.write_text(text.replace(first["episode_id"], "different_episode", 1), encoding="utf-8")
    else:
        record.write_text(text.split("\n\nResult\n", 1)[0], encoding="utf-8")
    result = Evaluator.evaluate_batch(path)
    expected = "incomplete" if damage == "missing_end" else "failed"
    assert result["episodes"][0]["status"] == expected
    assert result["aggregate"]["defeat"] == 0 and result["aggregate"]["tie"] == 0


def test_no_record_cannot_substantiate_a_cached_victory(tmp_path):
    batch = _batch(tmp_path)
    path = _index(batch, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["episodes"][0].update(record_directory=None, outcome="victory", result="Result.Victory")
    path.write_text(json.dumps(saved), encoding="utf-8")
    result = Evaluator.evaluate_batch(path)
    assert result["episodes"][0]["status"] == "incomplete"
    assert result["aggregate"]["victory"] == 0


def test_cli_errors_do_not_print_external_exception_secrets(tmp_path, monkeypatch, capsys):
    def fail_import(name):
        raise ImportError("secret credentials https://private.invalid")

    monkeypatch.setattr("sc2bench_env.benchmark.__main__.importlib.import_module", fail_import)
    with pytest.raises(SystemExit) as error:
        main(["run", "--suite", str(DEFAULT_SUITE), "--agent", "external:create_agent"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "secret" not in output.err and "private.invalid" not in output.err
    assert "ImportError" in output.err

"""Read-only preflight with safe reporting of failed native/client imports."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from sc2bench_env import diagnostics
from sc2bench_env.benchmark.__main__ import main


def test_fake_check_does_not_spawn_or_create_output(tmp_path, monkeypatch):
    root = tmp_path / "new-root"
    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", str(root))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Fake check must not spawn"))
    report = diagnostics.inspect_installation(backend="fake")
    assert report["status"] == "passed"
    assert not root.exists()


def test_child_report_isolated_and_legacy_warning_preserved(monkeypatch):
    def probe(command, **kwargs):
        assert command[0] and json.loads(command[-1]) == ["MapA", "MapB"]
        assert kwargs["timeout"] == 45
        return SimpleNamespace(returncode=0, stdout="other log\n" + diagnostics._REPORT_MARKER + json.dumps([
            diagnostics._check("import:sc2pathlib", "warning", "legacy", legacy_dependency=True)]))
    monkeypatch.setattr(subprocess, "run", probe)
    report = diagnostics.inspect_installation(map_names=["MapA", "MapB", "MapA"])
    assert report["status"] == "passed_with_warnings"
    assert report["legacy_dependencies"] == ["import:sc2pathlib"]


@pytest.mark.parametrize("mode", ["timeout", "crash", "bad_json", "no_report", "bad_schema"])
def test_child_failures_are_sanitized_errors(monkeypatch, mode):
    def probe(*args, **kwargs):
        if mode == "timeout":
            raise subprocess.TimeoutExpired("SECRET_TOKEN", 45)
        payload = {"crash": "SECRET_TOKEN", "bad_json": diagnostics._REPORT_MARKER + "{SECRET_TOKEN",
                   "no_report": "SECRET_TOKEN", "bad_schema": diagnostics._REPORT_MARKER + '[{"secret":"SECRET_TOKEN"}]'}
        return SimpleNamespace(returncode=1 if mode == "crash" else 0, stdout=payload[mode])
    monkeypatch.setattr(subprocess, "run", probe)
    report = diagnostics.inspect_installation()
    assert report["status"] == "failed" and report["error_count"] == 1
    assert "SECRET_TOKEN" not in json.dumps(report)


def test_invalid_output_override_is_reported(monkeypatch):
    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", "relative")
    report = diagnostics.inspect_installation(backend="fake")
    assert report["error_count"] == 1


def test_doctor_cli_human_and_json_outputs(capsys):
    assert main(["doctor", "--backend", "fake"]) == 0
    assert "[PASS]" in capsys.readouterr().out
    assert main(["doctor", "--backend", "fake", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_doctor_suite_checks_effective_unique_map(tmp_path, monkeypatch, capsys):
    from tests.helpers.terran_baseline import DEFAULT_SUITE
    seen = []
    def inspect(**kwargs):
        seen.extend(kwargs["map_names"])
        return {"error_count": 1}
    monkeypatch.setattr(diagnostics, "inspect_installation", inspect)
    assert main(["doctor", "--suite", str(DEFAULT_SUITE), "--json"]) == 1
    assert seen and set(seen) == {"KairosJunctionLE"}
    capsys.readouterr()

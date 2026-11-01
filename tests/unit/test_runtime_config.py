"""Packaged Sharpy configuration works without a project root or cwd config."""

import sys
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy import runtime_config


def test_packaged_defaults_without_source_root_ignore_cwd_config(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_config, "source_root", lambda: None)
    monkeypatch.delenv("SC2BENCH_CONFIG", raising=False)
    (tmp_path / "config.ini").write_text("[general]\ndebug=yes\ngame_step_size=99\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = runtime_config.get_config()
    assert config.getboolean("general", "debug") is False
    assert config.getint("general", "game_step_size") == 10
    assert runtime_config.get_version()[1] == "SC2Bench"


def test_source_overrides_and_local_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_config, "source_root", lambda: tmp_path)
    monkeypatch.delenv("SC2BENCH_CONFIG", raising=False)
    (tmp_path / "config.ini").write_text("[general]\ngame_step_size=7\n", encoding="utf-8")
    (tmp_path / "config-local.ini").write_text("[general]\ngame_step_size=8\n", encoding="utf-8")
    assert runtime_config.get_config().getint("general", "game_step_size") == 8
    assert runtime_config.get_config(local=False).getint("general", "game_step_size") == 7


def test_explicit_absolute_configuration_override(tmp_path, monkeypatch):
    path = tmp_path / "custom.ini"
    path.write_text("[general]\ngame_step_size=6\n", encoding="utf-8")
    monkeypatch.setenv("SC2BENCH_CONFIG", str(path))
    assert runtime_config.get_config().getint("general", "game_step_size") == 6


@pytest.mark.parametrize("value", ["", "relative.ini", "missing"])
def test_invalid_override_fails_without_fallback(tmp_path, monkeypatch, value):
    monkeypatch.setenv("SC2BENCH_CONFIG", str(tmp_path / value) if value == "missing" else value)
    with pytest.raises(ValueError):
        runtime_config.get_config()


def test_unrelated_config_module_is_not_overwritten(monkeypatch):
    external = SimpleNamespace(get_config=lambda: None)
    monkeypatch.setitem(sys.modules, "config", external)
    runtime_config.register_sharpy_config()
    assert sys.modules["config"] is external

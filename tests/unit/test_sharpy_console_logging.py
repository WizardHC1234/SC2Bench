"""Routine notices are quiet; diagnostic logs and other bots are untouched."""
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()

from sharpy.managers.core import LogManager
from sharpy.knowledges import KnowledgeBot
from sc2bench_env.backends.sharpy.bot import BenchBot
from sc2bench_env.backends.sharpy.log_manager import BenchLogManager


@pytest.mark.parametrize("tag", ["EnemyUnitsManager", "LostUnitsManager"])
@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
def test_routine_discovery_and_death_notices_are_not_emitted(tag, level):
    with patch.object(LogManager, "print") as emit:
        BenchLogManager().print("unit discovered/died", tag, log_level=level)
        emit.assert_not_called()


@pytest.mark.parametrize("tag", ["EnemyUnitsManager", "LostUnitsManager"])
@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR, logging.CRITICAL])
def test_important_logs_of_quiet_components_still_reach_sharpy(tag, level):
    with patch.object(LogManager, "print") as emit:
        BenchLogManager().print("important diagnostic", tag, stats=False, log_level=level)
        emit.assert_called_once_with("important diagnostic", tag=tag, stats=False, log_level=level)


@pytest.mark.parametrize("tag", [None, "Start", "ActManager", "LostUnitsContents"])
def test_unrelated_logs_and_end_statistics_are_unchanged(tag):
    with patch.object(LogManager, "print") as emit:
        BenchLogManager().print("normal message", tag)
        emit.assert_called_once_with("normal message", tag=tag, stats=True, log_level=logging.INFO)


def test_bench_bot_installs_its_own_manager_without_patching_shared_sharpy(monkeypatch):
    def initialize(bot, name):
        bot.knowledge = SimpleNamespace(log_manager=LogManager())
        bot.llm_observation_recorder = SimpleNamespace(enabled=True)
    monkeypatch.setattr(KnowledgeBot, "__init__", initialize)
    original_print = LogManager.print
    first = BenchBot(SimpleNamespace(), SimpleNamespace())
    second = BenchBot(SimpleNamespace(), SimpleNamespace())
    assert isinstance(first.knowledge.log_manager, BenchLogManager)
    assert first.knowledge.log_manager is not second.knowledge.log_manager
    assert LogManager.print is original_print
    assert type(LogManager()) is LogManager

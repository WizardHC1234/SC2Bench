"""Agent-independent, seed-free serial benchmark specifications."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import ENEMY_RACES, require_supported_own_race
from sc2bench_env.interface.opponents import normalize_opponent, require_enemy_style


_REQUIRED_CONFIG_FIELDS = {"race", "enemy_race", "map_name", "opponent",
                  "blocking_decisions", "decision_interval_seconds",
                  "game_time_limit_seconds"}
_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | {"enemy_style"}


def _positive_integer(value: Any, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_config(value: dict[str, Any]) -> None:
    if not _REQUIRED_CONFIG_FIELDS <= set(value) or set(value) - _CONFIG_FIELDS:
        raise ValueError("Each case must resolve all supported episode fields; seed/extra are not supported")
    require_supported_own_race(value["race"])
    require_enemy_style(value.get("enemy_style", "random"))
    if not isinstance(value["enemy_race"], str) or value["enemy_race"] not in ENEMY_RACES:
        raise ValueError("Unsupported enemy_race")
    if not isinstance(value["map_name"], str) or not value["map_name"].strip():
        raise ValueError("map_name must be nonempty")
    normalize_opponent(value["opponent"])
    if type(value["blocking_decisions"]) is not bool:
        raise ValueError("blocking_decisions must be a boolean")
    for field in ("decision_interval_seconds", "game_time_limit_seconds"):
        number = value[field]
        if type(number) not in {int, float} or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{field} must be a finite positive number")


@dataclass(frozen=True)
class BenchmarkSuite:
    """Validated JSON snapshot. Returned dictionaries cannot mutate the suite."""

    _canonical_json: str

    def __post_init__(self) -> None:
        data = json.loads(self._canonical_json)
        self._validate(data)
        for episode in [data["episode_defaults"]] + [case["episode"] for case in data["cases"]]:
            if "opponent" in episode:
                episode["opponent"] = normalize_opponent(episode["opponent"])
        object.__setattr__(self, "_canonical_json", json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))

    @staticmethod
    def _validate(data: Any) -> None:
        fields = {"schema_version", "suite_id", "suite_version", "purpose",
                  "repetitions", "max_decisions", "episode_defaults", "cases"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("Unsupported suite fields")
        if data["schema_version"] != "1":
            raise ValueError("Unsupported suite schema_version")
        for name in ("suite_id", "suite_version"):
            if not isinstance(data[name], str) or not data[name].strip():
                raise ValueError(f"{name} must be nonempty")
        if not isinstance(data["purpose"], str) or data["purpose"] not in {"workflow_pilot", "evaluation"}:
            raise ValueError("purpose must be workflow_pilot or evaluation")
        _positive_integer(data["repetitions"], "repetitions")
        _positive_integer(data["max_decisions"], "max_decisions")
        defaults = data["episode_defaults"]
        if not isinstance(defaults, dict) or set(defaults) - _CONFIG_FIELDS:
            raise ValueError("Unsupported episode_defaults fields; seed/extra are not supported")
        if not isinstance(data["cases"], list) or not data["cases"]:
            raise ValueError("Provide at least one suite case")
        seen = set()
        for case in data["cases"]:
            if not isinstance(case, dict) or set(case) != {"case_id", "episode"}:
                raise ValueError("Each case requires case_id and episode")
            case_id = case["case_id"]
            if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
                raise ValueError("case_id must be nonempty and unique")
            seen.add(case_id)
            if not isinstance(case["episode"], dict) or set(case["episode"]) - _CONFIG_FIELDS:
                raise ValueError("Unsupported case episode fields; seed/extra are not supported")
            _validate_config({**defaults, **case["episode"]})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkSuite":
        cls._validate(data)
        return cls(json.dumps(data, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False))

    @classmethod
    def load(cls, path: str | Path) -> "BenchmarkSuite":
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate suite JSON field")
                result[key] = value
            return result
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=unique_object)
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._canonical_json.encode("utf-8")).hexdigest()

    @property
    def max_decisions(self) -> int:
        return self.to_dict()["max_decisions"]

    def with_overrides(self, *, repetitions: Optional[int] = None,
                       max_decisions: Optional[int] = None,
                       opponents: Optional[tuple[str, ...]] = None,
                       enemy_style: Optional[str] = None) -> "BenchmarkSuite":
        data = self.to_dict()
        if repetitions is not None:
            data["repetitions"] = repetitions
        if max_decisions is not None:
            data["max_decisions"] = max_decisions
        if enemy_style is not None:
            data["episode_defaults"]["enemy_style"] = require_enemy_style(enemy_style)
            for case in data["cases"]:
                # Explicit CLI override applies even to cases with their own style.
                case["episode"].pop("enemy_style", None)
        if opponents is not None:
            opponents = tuple(normalize_opponent(opponent) for opponent in opponents)
            defaults = data["episode_defaults"]
            available = {case["episode"].get("opponent", defaults.get("opponent"))
                         for case in data["cases"]}
            if not opponents or set(opponents) - available:
                raise ValueError("Selected opponents must exist in the suite")
            data["cases"] = [case for case in data["cases"] if
                             case["episode"].get("opponent", defaults.get("opponent")) in opponents]
        return self.from_dict(data)

    def episode_plan(self) -> list[dict[str, Any]]:
        data = self.to_dict()
        return [{"case_id": case["case_id"], "repetition": repetition,
                 "config": EpisodeConfig(**{**data["episode_defaults"], **case["episode"]})}
                for case in data["cases"]
                for repetition in range(1, data["repetitions"] + 1)]

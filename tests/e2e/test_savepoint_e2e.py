"""Live SC2 check that one savepoint can replay two branches."""
from __future__ import annotations

import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.backends.sharpy.engine_fingerprint import describe_fingerprint_diff
from sc2bench_env.runtime.savepoint import SavepointError, describe_engine
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float) -> dict:
    return {"name": "advance", "arguments": {"seconds": seconds}}


def _train_scv(count: int = 2) -> dict:
    return {"name": "train", "arguments": {"target": "scv", "count": count}}


def _build_depot() -> dict:
    return {"name": "build", "arguments": {"target": "supply_depot"}}


def _detail(env):
    """Return the full simulation hash and the fingerprint object captured with it."""
    digest = env.backend.capture_engine_detail()
    return digest, env.backend._bridge.last_fingerprint


def _unit_rows(fingerprint):
    from google.protobuf.json_format import MessageToDict
    from s2clientprotocol import raw_pb2

    rows = []
    for tag, blob in fingerprint.units.items():
        data = MessageToDict(raw_pb2.Unit.FromString(blob), preserving_proto_field_name=True)
        rows.append((
            tag,
            data.get("unit_type"),
            data.get("alliance"),
            data.get("display_type"),
            data.get("pos"),
        ))
    return rows


def _same_world(saved_digest, saved_fingerprint, loaded_digest, loaded_fingerprint):
    if loaded_digest != saved_digest:
        saved_tags = set(saved_fingerprint.units)
        loaded_tags = set(loaded_fingerprint.units)
        rows = {row[0]: row for row in _unit_rows(saved_fingerprint)}
        rows.update({row[0]: row for row in _unit_rows(loaded_fingerprint)})
        missing = [rows[tag] for tag in sorted(saved_tags - loaded_tags)]
        extra = [rows[tag] for tag in sorted(loaded_tags - saved_tags)]
        raise AssertionError(
            describe_fingerprint_diff(saved_fingerprint, loaded_fingerprint)
            + f"\nmissing {missing[:8]}\nextra {extra[:8]}"
        )


def _signature(env) -> dict:
    snapshot = env.backend.snapshot()
    return {
        "time": round(float(snapshot.game_time_seconds), 3),
        "minerals": int(snapshot.minerals),
        "vespene": int(snapshot.vespene),
        "supply_used": int(snapshot.supply_used),
        "supply_cap": int(snapshot.supply_cap),
        "units": dict(snapshot.units),
        "buildings": dict(snapshot.buildings),
        "under_construction": dict(snapshot.info.get("under_construction") or {}),
        "upgrades": sorted(str(item) for item in (snapshot.info.get("upgrades") or [])),
        "terminated": bool(snapshot.terminated),
        "scv": int(snapshot.units.get("scv", 0)),
    }


def test_live_savepoint_restores_and_repeats_a_branch(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(
            opponent="veryeasy",
            enemy_style="macro",
            seed=7,
            game_time_limit_seconds=420,
        ))
        before = _signature(env)
        for _ in range(10):
            drive(env, [_wait(5)])
            before = _signature(env)
            if before["minerals"] >= 200:
                break
        assert before["minerals"] >= 200, before
        save = env.create_savepoint(label="eight_seconds", context={"decision": 1})
        saved_fingerprint = env.backend._bridge.engine_fingerprint
        saved_detail = env.backend.saved_engine_detail()
        assert len(saved_fingerprint.units) >= 20
        current_detail, current_fingerprint = _detail(env)
        _same_world(saved_detail, saved_fingerprint, current_detail, current_fingerprint)
        assert save.base_game_time_seconds == pytest.approx(before["time"], abs=0.05)
        assert _signature(env) == before
        assert env.saved_context(save) == {"decision": 1}

        def train_branch(current):
            drive(current, [_train_scv(2), _wait(15)])
            signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint

        # Continuing from the save must match a later restore of the same orders.
        continued_signature, continued_detail, continued_fingerprint = train_branch(env)
        assert continued_signature["time"] > before["time"] + 10
        assert continued_signature["scv"] >= before["scv"]
        assert continued_detail != saved_detail

        first_signature, first_detail, first_fingerprint = env.run_branch(
            save, branch_id="train_a", policy=train_branch,
        )
        assert first_signature == continued_signature
        _same_world(
            continued_detail, continued_fingerprint, first_detail, first_fingerprint,
        )

        second_signature, second_detail, second_fingerprint = env.run_branch(
            save, branch_id="train_b", policy=train_branch,
        )
        assert second_signature == first_signature
        _same_world(first_detail, first_fingerprint, second_detail, second_fingerprint)

        restored = env.restore_savepoint(save, branch_id="after_train")
        assert restored.game_time_seconds == pytest.approx(before["time"], abs=0.05)
        assert restored.game_time_seconds > 1
        assert _signature(env) == before, describe_engine(env.backend.snapshot())
        restored_detail, restored_fingerprint = _detail(env)
        _same_world(saved_detail, saved_fingerprint, restored_detail, restored_fingerprint)

        def build_branch(current):
            drive(current, [_build_depot(), _wait(6)])
            signature = _signature(current)
            for _ in range(6):
                if signature["under_construction"].get("supply_depot", 0) >= 1:
                    break
                if signature["buildings"].get("supply_depot", 0) >= 1:
                    break
                drive(current, [_wait(6)])
                signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint

        def economy_branch(current):
            drive(current, [_train_scv(1), _wait(12)])
            signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint

        build_signature, build_detail, build_fingerprint = env.run_branch(
            save, branch_id="build_a", policy=build_branch,
        )
        assert build_signature["time"] > before["time"]
        depot_started = (
            build_signature["under_construction"].get("supply_depot", 0) >= 1
            or build_signature["buildings"].get("supply_depot", 0) >= 1
        )
        assert depot_started, build_signature
        assert build_detail != saved_detail

        other_signature, other_detail, other_fingerprint = env.run_branch(
            save, branch_id="economy", policy=economy_branch,
        )
        assert other_detail != build_detail
        assert other_signature["under_construction"].get("supply_depot", 0) == 0
        assert other_fingerprint is not None

        env.restore_savepoint(save, branch_id="after_split")
        assert _signature(env) == before, describe_engine(env.backend.snapshot())
        split_detail, split_fingerprint = _detail(env)
        _same_world(saved_detail, saved_fingerprint, split_detail, split_fingerprint)

        repeat_signature, repeat_detail, repeat_fingerprint = env.run_branch(
            save, branch_id="build_b", policy=build_branch,
        )
        assert repeat_signature == build_signature
        _same_world(build_detail, build_fingerprint, repeat_detail, repeat_fingerprint)

        def _scout_zone(current) -> str:
            rows = current.backend.snapshot().info.get("zone_state") or []
            for row in rows:
                zone_id = row.get("zone_id")
                if isinstance(zone_id, str) and row.get("known_owner") != "self":
                    return zone_id
            raise AssertionError(rows)

        def scout_branch(current):
            zone = _scout_zone(current)
            drive(current, [
                {"name": "scout", "arguments": {"route": [zone]}},
                _wait(12),
            ])
            signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint, zone

        env.restore_savepoint(save, branch_id="before_scout")
        # This play has already been through a load. The no-load comparison is
        # test_live_scout_continuation_matches_restore.
        scout_signature, scout_detail, scout_fingerprint, scout_zone = env.run_branch(
            save, branch_id="scout_a", policy=scout_branch,
        )
        assert scout_detail != saved_detail
        repeat_scout, repeat_scout_detail, repeat_scout_fingerprint, repeat_zone = env.run_branch(
            save, branch_id="scout_b", policy=scout_branch,
        )
        assert repeat_zone == scout_zone
        assert repeat_scout == scout_signature
        _same_world(scout_detail, scout_fingerprint, repeat_scout_detail, repeat_scout_fingerprint)

        for name in ("train_a", "train_b", "build_a", "build_b", "economy", "scout_a", "scout_b"):
            assert (env.record_path / "branches" / name / "branch.json").is_file()
        trunk = (env.record_path / "session.json").read_text(encoding="utf-8")
        env.close()
        assert (env.record_path / "session.json").read_text(encoding="utf-8") == trunk
    finally:
        env.close()


def test_live_time_limit_returns_to_the_savepoint(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(
            opponent="veryeasy",
            enemy_style="macro",
            seed=7,
            game_time_limit_seconds=20,
        ))
        drive(env, [_wait(5)])
        save = env.create_savepoint(label="before_clock")
        saved_fingerprint = env.backend._bridge.engine_fingerprint
        saved_detail = env.backend.saved_engine_detail()
        before = _signature(env)
        drive(env, [_wait(30)])
        assert env.backend.snapshot().terminated is True
        assert env.backend.snapshot().end_reason == "time_limit"
        restored = env.restore_savepoint(save, branch_id="after_clock")
        assert restored.game_time_seconds == pytest.approx(before["time"], abs=0.05)
        assert _signature(env) == before
        assert env.backend.snapshot().terminated is False
        loaded_detail, loaded_fingerprint = _detail(env)
        _same_world(saved_detail, saved_fingerprint, loaded_detail, loaded_fingerprint)
        replay = env.record_path / "replay.SC2Replay"
        assert replay.is_file() and replay.stat().st_size > 0
        replay_size = replay.stat().st_size
        env.run_branch(save, branch_id="after_replay", policy=lambda current: drive(current, [_wait(2)]))
        assert replay.stat().st_size == replay_size
        assert (env.record_path / "branches" / "after_replay" / "branch.json").is_file()
    finally:
        env.close()


def test_live_decided_match_cannot_be_loaded(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(
            opponent="veryeasy",
            enemy_style="macro",
            seed=7,
            game_time_limit_seconds=420,
        ))
        drive(env, [_wait(2)])
        save = env.create_savepoint(label="before_result")
        env.backend.debug_end_match()
        drive(env, [_wait(2)])
        snapshot = env.backend.snapshot()
        assert snapshot.terminated is True
        assert snapshot.end_reason != "time_limit"
        assert str(snapshot.result).startswith("Result.")
        with pytest.raises(SavepointError, match="already ended|not paused"):
            env.restore_savepoint(save, branch_id="after_result")
    finally:
        env.close()


def _attack_zone(env) -> str:
    rows = env.backend.snapshot().info.get("zone_state") or []
    for row in rows:
        zone_id = row.get("zone_id")
        if isinstance(zone_id, str) and row.get("known_owner") != "self":
            return zone_id
    raise AssertionError(rows)


def _until(env, ready, limit: int = 14):
    signature = _signature(env)
    for _ in range(limit):
        if ready(signature):
            return signature
        drive(env, [_wait(6)])
        signature = _signature(env)
    return signature


def test_live_scout_continuation_matches_restore(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(
            opponent="veryeasy",
            enemy_style="macro",
            seed=7,
            game_time_limit_seconds=420,
        ))
        drive(env, [_wait(5)])
        save = env.create_savepoint(label="before_scout")

        def scout(current):
            zone = _attack_zone(current)
            drive(current, [
                {"name": "scout", "arguments": {"route": [zone]}},
                _wait(12),
            ])
            signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint, zone

        continued_signature, continued_detail, continued_fingerprint, zone = scout(env)
        assert continued_detail != env.backend.saved_engine_detail()
        loaded_signature, loaded_detail, loaded_fingerprint, loaded_zone = env.run_branch(
            save, branch_id="scout_loaded", policy=scout,
        )
        assert loaded_zone == zone
        assert loaded_signature == continued_signature
        _same_world(continued_detail, continued_fingerprint, loaded_detail, loaded_fingerprint)
    finally:
        env.close()


def test_live_combat_branch_repeats(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(
            opponent="veryeasy",
            enemy_style="macro",
            seed=7,
            game_time_limit_seconds=420,
        ))
        before = _signature(env)
        for _ in range(12):
            drive(env, [_wait(5)])
            before = _signature(env)
            if before["minerals"] >= 250:
                break
        assert before["minerals"] >= 250, before
        save = env.create_savepoint(label="before_fight")
        saved_detail = env.backend.saved_engine_detail()
        saved_fingerprint = env.backend._bridge.engine_fingerprint

        def fight(current):
            drive(current, [{"name": "build", "arguments": {"target": "supply_depot"}}, _wait(6)])
            depot = _until(
                current,
                lambda row: row["buildings"].get("supply_depot", 0) >= 1,
            )
            assert depot["buildings"].get("supply_depot", 0) >= 1, depot
            drive(current, [{"name": "build", "arguments": {"target": "barracks"}}, _wait(6)])
            barracks = _until(
                current,
                lambda row: row["buildings"].get("barracks", 0) >= 1,
            )
            assert barracks["buildings"].get("barracks", 0) >= 1, barracks
            drive(current, [{"name": "train", "arguments": {"target": "marine", "count": 2}}, _wait(6)])
            army = _until(current, lambda row: row["units"].get("marine", 0) >= 2)
            assert army["units"].get("marine", 0) >= 2, army
            zone = _attack_zone(current)
            drive(current, [
                {"name": "combat", "arguments": {
                    "style": "attack", "target": zone, "units": {"marine": 2},
                }},
                _wait(12),
            ])
            signature = _signature(current)
            digest, fingerprint = _detail(current)
            return signature, digest, fingerprint, zone

        continued_signature, continued_detail, continued_fingerprint, zone = fight(env)
        assert continued_signature["units"].get("marine", 0) >= 2
        assert continued_detail != saved_detail
        first_signature, first_detail, first_fingerprint, first_zone = env.run_branch(
            save, branch_id="fight_a", policy=fight,
        )
        assert first_zone == zone
        assert first_signature == continued_signature
        _same_world(continued_detail, continued_fingerprint, first_detail, first_fingerprint)
        assert first_detail != saved_detail
        second_signature, second_detail, second_fingerprint, second_zone = env.run_branch(
            save, branch_id="fight_b", policy=fight,
        )
        assert second_zone == zone
        assert second_signature == first_signature
        _same_world(first_detail, first_fingerprint, second_detail, second_fingerprint)
        env.restore_savepoint(save, branch_id="after_fight")
        assert _signature(env) == before
        restored_detail, restored_fingerprint = _detail(env)
        _same_world(saved_detail, saved_fingerprint, restored_detail, restored_fingerprint)
    finally:
        env.close()

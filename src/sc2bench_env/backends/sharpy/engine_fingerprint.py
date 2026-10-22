"""Exact SC2 observation identity for one savepoint.

The raw game loop is not part of the identity: quick_load can return it to 0
while the simulation itself is unchanged. Selection and on-screen flags are
client view, not the match. Fog snapshots keep their position and type, but
not their tag: seeing the same mineral again after a load mints a new one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from google.protobuf.json_format import MessageToDict
from s2clientprotocol import raw_pb2


_UI_UNIT_FIELDS = ("is_selected", "is_on_screen")
_SCORE_RATE_FIELDS = ("current_apm", "current_effective_apm")


@dataclass
class EngineFingerprint:
    """Byte identity of the simulation frame, plus the unit records used to explain a miss."""

    digest: str
    units: Dict[int, bytes] = field(default_factory=dict)
    rest: Dict[str, bytes] = field(default_factory=dict)


def fingerprint_observation(observation) -> EngineFingerprint:
    """Hash the world stored in one Observation proto."""
    units: Dict[int, bytes] = {}
    unit_blobs: List[bytes] = []
    snapshot_blobs: List[bytes] = []
    for unit in sorted(observation.raw_data.units, key=lambda item: int(item.tag)):
        if unit.display_type == raw_pb2.Snapshot:
            snapshot_blobs.append(_canonical_snapshot(unit).SerializeToString())
            continue
        canonical = _canonical_unit(unit)
        blob = canonical.SerializeToString()
        units[int(unit.tag)] = blob
        unit_blobs.append(blob)
    rest = {
        "player_common": observation.player_common.SerializeToString(),
        "map_state": observation.raw_data.map_state.SerializeToString(),
        "player": observation.raw_data.player.SerializeToString(),
        "effects": _sorted_message_blob(observation.raw_data.effects),
        "radar": _sorted_message_blob(observation.raw_data.radar),
        "score": _canonical_score(observation.score),
        "snapshots": b"\0".join(sorted(snapshot_blobs)),
    }
    digest = hashlib.sha256()
    digest.update(b"\0".join(unit_blobs))
    for name in sorted(rest):
        digest.update(name.encode("utf-8"))
        digest.update(rest[name])
    return EngineFingerprint(digest=digest.hexdigest(), units=units, rest=rest)


def describe_fingerprint_diff(saved: EngineFingerprint, loaded: EngineFingerprint) -> str:
    """Name the first simulation fields that quick_load did not restore."""
    lines: List[str] = []
    saved_tags = set(saved.units)
    loaded_tags = set(loaded.units)
    missing = sorted(saved_tags - loaded_tags)
    extra = sorted(loaded_tags - saved_tags)
    if missing:
        lines.append(f"missing unit tags {missing[:8]}")
    if extra:
        lines.append(f"extra unit tags {extra[:8]}")
    for tag in sorted(saved_tags & loaded_tags):
        if saved.units[tag] == loaded.units[tag]:
            continue
        lines.append(_unit_diff(tag, saved.units[tag], loaded.units[tag]))
        if len(lines) >= 8:
            break
    for name in sorted(set(saved.rest) | set(loaded.rest)):
        if saved.rest.get(name) != loaded.rest.get(name):
            lines.append(f"{name} differs")
    return "\n".join(lines[:12]) or "simulation frame differs"


def _canonical_unit(unit):
    clone = type(unit)()
    clone.CopyFrom(unit)
    for name in _UI_UNIT_FIELDS:
        if hasattr(clone, name):
            setattr(clone, name, False)
    return clone


def _canonical_snapshot(unit):
    """Fog placeholders are the same mineral or unit even when the tag changes."""
    clone = _canonical_unit(unit)
    clone.tag = 0
    return clone


def _canonical_score(score) -> bytes:
    clone = type(score)()
    clone.CopyFrom(score)
    details = getattr(clone, "score_details", None)
    if details is not None:
        for name in _SCORE_RATE_FIELDS:
            if hasattr(details, name):
                setattr(details, name, 0)
    return clone.SerializeToString()


def _sorted_message_blob(messages: Iterable) -> bytes:
    blobs = sorted((message.SerializeToString() for message in messages))
    return b"\0".join(blobs)


def _unit_diff(tag: int, saved_blob: bytes, loaded_blob: bytes) -> str:
    from s2clientprotocol import raw_pb2

    saved = _unit_dict(raw_pb2.Unit.FromString(saved_blob))
    loaded = _unit_dict(raw_pb2.Unit.FromString(loaded_blob))
    keys = sorted(set(saved) | set(loaded))
    changed = [key for key in keys if saved.get(key) != loaded.get(key)]
    details = []
    for key in changed[:6]:
        details.append(f"{key}: {saved.get(key)!r} -> {loaded.get(key)!r}")
    where = saved.get("pos") or loaded.get("pos")
    return f"unit {tag} at {where} changed {', '.join(details)}"


def _unit_dict(unit) -> dict:
    return MessageToDict(unit, preserving_proto_field_name=True)


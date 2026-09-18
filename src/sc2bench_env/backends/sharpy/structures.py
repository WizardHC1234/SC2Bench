"""Stable structure object IDs for object-level actions (e.g. CC upgrade)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StructureRegistry:
    """Maps SC2 unit tags to stable platform object ids within an episode.

    Object ids are the only Agent-visible structure identifiers. Internal demand
    UUIDs stay hidden; these ids are for actions like upgrade that must name a
    concrete building.
    """

    _tag_to_id: Dict[int, str] = field(default_factory=dict)
    _id_to_tag: Dict[str, int] = field(default_factory=dict)
    _prefix_counters: Dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self._tag_to_id.clear()
        self._id_to_tag.clear()
        self._prefix_counters.clear()

    def id_for_tag(self, tag: int, *, prefix: str = "cc") -> str:
        existing = self._tag_to_id.get(int(tag))
        if existing is not None:
            return existing
        index = self._prefix_counters.get(prefix, 0)
        object_id = f"{prefix}_{index}"
        self._prefix_counters[prefix] = index + 1
        self._tag_to_id[int(tag)] = object_id
        self._id_to_tag[object_id] = int(tag)
        return object_id

    def tag_for_id(self, object_id: str) -> Optional[int]:
        return self._id_to_tag.get(object_id)

    def sync_townhalls(
        self,
        townhalls: List[tuple[int, str]],
    ) -> List[dict]:
        """Ensure each townhall tag has an id; return Agent-visible records.

        Args:
            townhalls: list of (tag, platform_type_name)
        """
        living_tags = {int(tag) for tag, _ in townhalls}
        # Drop destroyed tags so ids are not reused for a different building.
        for tag, object_id in list(self._tag_to_id.items()):
            if tag not in living_tags and object_id.startswith("cc_"):
                self._tag_to_id.pop(tag, None)
                self._id_to_tag.pop(object_id, None)

        records: List[dict] = []
        for tag, type_name in townhalls:
            object_id = self.id_for_tag(int(tag), prefix="cc")
            records.append({"id": object_id, "type": type_name})
        records.sort(key=lambda row: row["id"])
        return records

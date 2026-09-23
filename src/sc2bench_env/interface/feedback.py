"""Action receipt and decision feedback structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class ActionReceipt:
    """Per-action result returned in Feedback after step().

    Does not expose internal demand UUIDs to the Agent.
    """

    action: str
    result: str
    target: Optional[str] = None
    count: Optional[int] = None
    target_action: Optional[str] = None
    reason: Optional[str] = None
    action_id: Optional[str] = None
    style: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    group: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for key in ("target", "count", "target_action", "style", "group"):
            value = getattr(self, key)
            if value is not None:
                arguments[key] = value
        payload: dict[str, Any] = {
            "name": self.action,
            "arguments": arguments,
            "result": self.result,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.action_id is not None:
            payload["action_id"] = self.action_id
        if self.details is not None:
            payload["details"] = self.details
        return payload


@dataclass
class Feedback:
    """Batch feedback for one decision round."""

    receipts: List[ActionReceipt] = field(default_factory=list)
    events: List[dict[str, Any]] = field(default_factory=list)
    name_normalizations: List[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "events": list(self.events),
        }
        if self.name_normalizations:
            payload["name_normalizations"] = list(self.name_normalizations)
        return payload

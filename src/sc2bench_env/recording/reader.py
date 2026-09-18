"""Read and reconstruct a compact SC2Bench episode."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict


def read_episode(directory: str | Path) -> Dict[str, Any]:
    path = Path(directory)
    compact = path / "interactions.jsonl"
    rows = [json.loads(line) for line in compact.read_text(encoding="utf-8").splitlines()]
    if not rows or rows[0].get("type") != "episode_start":
        raise ValueError("Missing episode_start in interactions.jsonl")
    metadata = {key: value for key, value in rows[0].items()
                if key not in {"type", "platform_prompt_sha256", "platform_prompt_char_count"}}
    prompt_part = (path / "episode.txt").read_text(encoding="utf-8").split("\n\nPlatform prompt\n", 1)[1]
    prompt = prompt_part[:rows[0]["platform_prompt_char_count"]]
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != rows[0]["platform_prompt_sha256"]:
        raise ValueError("episode.txt platform prompt does not match interactions.jsonl")
    system_messages = {rows[0]["platform_prompt_sha256"]: prompt}
    system_messages.update({row["id"]: row["content"] for row in rows if row.get("type") == "system_message"})
    steps = []
    interactions = []
    for row in rows[1:]:
        if row.get("type") == "system_message":
            continue
        entry = dict(row)
        interaction = entry.pop("agent_interaction", None)
        steps.append(entry)
        if interaction is not None:
            for messages in (interaction.get("input", {}).get("messages"),
                             interaction.get("messages_transcript")):
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    if isinstance(message, dict) and "content_ref" in message:
                        message["content"] = system_messages[message.pop("content_ref")]
            interactions.append(interaction)
    return {
        "metadata": metadata,
        "platform_prompt": prompt,
        "steps": steps,
        "interactions": interactions,
        "summary": next(({key: value for key, value in row.items()
                          if key not in {"type", "observation"}}
                         for row in reversed(steps) if row.get("type") == "end"), None),
    }

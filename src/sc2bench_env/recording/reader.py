"""Read an episode's configuration, prompt, result, and saved session."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def parse_episode_text(text: str) -> Tuple[Dict[str, Any], str, Optional[Dict[str, Any]]]:
    """Split episode.txt into configuration, the platform prompt, and an optional result."""
    marker = "\n\nPlatform prompt\n"
    config_marker = "\nConfiguration and versions\n"
    if marker not in text or config_marker not in text:
        raise ValueError("Missing episode configuration")
    head, rest = text.split(marker, 1)
    metadata = json.loads(head.split(config_marker, 1)[1])
    if not isinstance(metadata, dict):
        raise ValueError("Invalid episode configuration")
    count = metadata.get("platform_prompt_char_count")
    if type(count) is not int or count < 0 or count > len(rest):
        raise ValueError("Invalid platform prompt length")
    prompt = rest[:count]
    tail = rest[count:]
    result_marker = "\n\nResult\n"
    summary = None
    if tail.startswith(result_marker):
        summary = json.loads(tail[len(result_marker):])
        if not isinstance(summary, dict):
            raise ValueError("Invalid episode result")
    return metadata, prompt, summary


def read_episode(directory: str | Path) -> Dict[str, Any]:
    path = Path(directory)
    metadata, prompt, summary = parse_episode_text(
        (path / "episode.txt").read_text(encoding="utf-8"))
    session_path = path / "session.json"
    if session_path.is_file():
        session = json.loads(session_path.read_text(encoding="utf-8"))
        if not isinstance(session, dict) or not isinstance(session.get("messages"), list):
            raise ValueError("Invalid session.json")
        session.setdefault("tools", None)
    else:
        session = {
            "episode_id": metadata.get("episode_id"),
            "model": None,
            "result": None,
            "tools": None,
            "messages": [],
        }
    return {
        "metadata": metadata,
        "platform_prompt": prompt,
        "summary": summary,
        "session": session,
        "messages": session["messages"],
    }

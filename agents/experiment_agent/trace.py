"""Human-readable actual model outputs, captured by the platform's log.txt tee."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

BACKFILL_MARKER = "[SC2Bench session transcript BEGIN]"
BACKFILL_END = "[SC2Bench session transcript END]"
LEGACY_MARKER = "===== SC2Bench 模型会话补录 BEGIN ====="
LEGACY_END = "===== SC2Bench 模型会话补录 END ====="


def block(title, value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return f"\n[{title}]\n{value if value else '(empty)'}\n"


def emit(title, value):
    # stderr remains human-readable; stdout can still carry CLI JSON receipts.
    # Both streams are copied into log.txt by the existing platform recorder.
    try:
        print(block(title, value), file=sys.stderr, flush=True)
    except (OSError, UnicodeError, TypeError, ValueError):
        pass


def reply_blocks(response, role):
    yield "Model output | " + role, response.get("content") or ""
    if response.get("reasoning"):
        yield "API reasoning | returned by provider", response["reasoning"]
    if response.get("tool_calls"):
        yield "Tool calls | " + role, response["tool_calls"]


def attributed_replies(messages, model_calls=None):
    """Use saved call roles, without requiring role tags in model instructions.

    The prefix parser is only a fallback for old transcripts without metrics.
    """
    roles = None if model_calls is None else [call["role"] for call in model_calls if call.get("status") == "ok"]
    if roles is not None and len(roles) != sum(m["role"] == "assistant" for m in messages):
        raise ValueError("Saved model call roles do not match assistant replies")
    result, previous_user, legacy_role = [], "", "actor"
    for index, message in enumerate(messages):
        content = message.get("content") or ""
        if message["role"] in ("user", "system"):
            previous_user = content
            match = re.match(r"\[Stage: (actor|executor|planner|replanner|reflector)", content)
            if match:
                legacy_role = match[1]
        elif message["role"] == "assistant":
            result.append({"role": roles[len(result)] if roles is not None else legacy_role,
                           "content": content, "instruction": previous_user, "message_index": index})
    return result


def accepted_auxiliary_replies(messages, model_calls):
    replies = attributed_replies(messages, model_calls)
    return [{**reply, "next_instruction": following["instruction"]}
            for reply, following in zip(replies, replies[1:])
            if reply["role"] in {"planner", "replanner", "reflector"}
            and following["role"] in {"actor", "executor"}]


def render_session(messages, model_calls=None):
    """Render saved messages verbatim. No model is called and no thought is inferred."""
    parts = [BACKFILL_MARKER + "\nSource: session.json; saved messages in original order.\n"]
    roles = {reply["message_index"]: reply["role"] for reply in attributed_replies(messages, model_calls)}
    call_number = 0
    for index, message in enumerate(messages):
        content = message.get("content") or ""
        if message["role"] == "user":
            if content.startswith("[Current Observation]"):
                parts.append(block("Observation and feedback", content))
            elif "[Harness feedback]" in content or content.startswith("[Harness control]"):
                parts.append(block("Harness feedback", content))
        elif message["role"] == "assistant":
            role = roles[index]
            call_number += 1
            response = {"content": content}
            if message.get("reasoning_content") or message.get("reasoning"):
                response["reasoning"] = message.get("reasoning_content") or message["reasoning"]
            if message.get("tool_calls"):
                response["tool_calls"] = [{"id": call["id"], "name": call["function"]["name"],
                                           "arguments": call["function"]["arguments"]}
                                          for call in message["tool_calls"]]
            parts.extend(block(f"{title} | reply #{call_number}", value)
                         for title, value in reply_blocks(response, role))
        elif message["role"] == "tool":
            parts.append(block("Tool result | " + message["tool_call_id"], content))
    parts.append("\n" + BACKFILL_END + "\n")
    return "".join(parts)


def append_saved_session(episode_dir):
    episode_dir = Path(episode_dir)
    session = json.loads((episode_dir / "session.json").read_text(encoding="utf-8"))
    metrics_path = episode_dir / "metrics.json"
    calls = json.loads(metrics_path.read_text(encoding="utf8")).get("model_calls") if metrics_path.exists() else None
    log_path = episode_dir / "log.txt"
    old = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    if BACKFILL_MARKER in old:
        return False
    if LEGACY_MARKER in old:
        prefix, _, legacy = old.partition(LEGACY_MARKER)
        _, end, suffix = legacy.partition(LEGACY_END)
        if not end:
            raise ValueError("Incomplete legacy transcript section; original log preserved")
        replacement = prefix + render_session(session["messages"], calls) + suffix
        with log_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(replacement)
        return True
    with log_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write("\n" + render_session(session["messages"], calls))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append saved model messages to completed episode logs; no API/game calls.")
    parser.add_argument("--records", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.records.is_dir():
        parser.error("records directory does not exist")
    updated = 0
    for session_path in sorted(args.records.rglob("session.json")):
        batch_path = session_path.parent.parent.parent / "batch.json"
        if batch_path.exists() and json.loads(batch_path.read_text(encoding="utf-8")).get("status") == "running":
            continue
        updated += int(append_saved_session(session_path.parent))
    print(json.dumps({"updated_logs": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()

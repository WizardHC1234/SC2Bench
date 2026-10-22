"""Codex SDK helper. Runs on Python 3.10+ and is not imported by the 3.9 match process."""
from __future__ import annotations


import json
import sys


def main() -> int:
    try:
        from openai_codex import Codex, Sandbox
    except ImportError:
        print(json.dumps({"ok": False, "error": "openai-codex requires Python 3.10+"}), flush=True)
        return 2
    codex = Codex()
    codex.__enter__()
    thread = None
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            message = json.loads(line)
            operation = message.get("op")
            if operation == "close":
                break
            if operation == "open":
                thread = codex.thread_start(
                    model=message.get("model"),
                    sandbox=Sandbox.read_only,
                    cwd=message.get("cwd"),
                    config={"approval_policy": "never"},
                )
                result = thread.run(message.get("message") or "")
                print(json.dumps({
                    "ok": True,
                    "thread_id": thread.id,
                    "final": getattr(result, "final_response", None),
                }), flush=True)
                continue
            if operation == "turn":
                if thread is None:
                    print(json.dumps({"ok": False, "error": "no thread"}), flush=True)
                    return 2
                result = thread.run(message.get("message") or "")
                print(json.dumps({
                    "ok": True,
                    "thread_id": thread.id,
                    "final": getattr(result, "final_response", None),
                }), flush=True)
                continue
            print(json.dumps({"ok": False, "error": "unknown op"}), flush=True)
            return 2
    finally:
        codex.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

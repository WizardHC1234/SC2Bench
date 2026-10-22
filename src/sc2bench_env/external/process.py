"""Stop an external Agent process without leaving its children running."""
from __future__ import annotations


import subprocess
import sys
from typing import Optional


def text_pipe_kwargs() -> dict[str, object]:
    """Read Agent output as UTF-8. Windows otherwise decodes pipes as GBK."""
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


def stop_process(process: Optional[subprocess.Popen], *, grace_seconds: float = 2.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass

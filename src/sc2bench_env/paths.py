"""One output-path policy for Environment, Runner and all example entry points."""
from __future__ import annotations


import os
import shutil
import stat
from pathlib import Path
from typing import Optional


def default_output_root() -> Path:
    """Never derive implicit output paths from the caller's working directory.

    Source/editable installs keep the project's existing output layout. A wheel
    install uses a user-writable directory instead of writing to site-packages.
    No directories are created merely by resolving these paths.
    """
    override = os.environ.get("SC2BENCH_OUTPUT_DIR")
    if override is not None:
        path = Path(override.strip()).expanduser()
        if not override.strip() or not path.is_absolute():
            raise ValueError("SC2BENCH_OUTPUT_DIR must be a nonempty absolute path")
        return path.resolve()
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "sc2bench_env").is_dir():
        return candidate
    return Path.home() / ".sc2bench"


def resolve_record_dir(path: Optional[str | Path] = None) -> Path:
    return Path(path).expanduser().resolve() if path is not None else default_output_root() / "records"


def match_agent_directory(record_dir: str | Path) -> Path:
    """Codex files for one player, inside that player's episode record."""
    path = Path(record_dir).expanduser().resolve() / "agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_agent_workspace(work_dir: str, record_dir: Optional[str | Path]) -> None:
    """Drop the temporary agent files after their session has been copied into the record."""
    if record_dir is None:
        return
    path = Path(work_dir).resolve()
    record = Path(record_dir).resolve()
    if path.name != "agent" or record not in path.parents or not path.is_dir():
        return
    def _make_writable(function, target, _error) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onerror=_make_writable)


def default_paths() -> dict[str, str]:
    root = default_output_root()
    return {"output_root": str(root), "record_dir": str(root / "records")}


def record_reference(directory: str | Path, results_dir: str | Path) -> str:
    """Portable separators; cross-drive Windows overrides require an absolute path."""
    try:
        reference = Path(os.path.relpath(directory, results_dir))
    except ValueError:
        reference = Path(directory).resolve()
    return reference.as_posix()

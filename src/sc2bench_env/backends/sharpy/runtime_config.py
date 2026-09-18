"""Platform-owned Sharpy configuration, available in source and wheel installs."""

from __future__ import annotations

import os
from configparser import ConfigParser
from importlib import resources
from pathlib import Path


def source_root() -> Path | None:
    root = Path(__file__).resolve().parents[4]
    return root if (root / "pyproject.toml").is_file() and (root / "src" / "sc2bench_env").is_dir() else None


def get_config(local: bool = True) -> ConfigParser:
    """Package defaults, optional source overrides, then explicit absolute override."""
    config = ConfigParser()
    config.read_string(resources.files("sc2bench_env.backends.sharpy").joinpath("default.ini").read_text(encoding="utf-8"))
    root = source_root()
    if root is not None:
        config.read([str(root / "config.ini")] + ([str(root / "config-local.ini")] if local else []), encoding="utf-8")
    override = os.environ.get("SC2BENCH_CONFIG")
    if override is not None:
        path = Path(override.strip()).expanduser()
        if not override.strip() or not path.is_absolute() or not path.is_file():
            raise ValueError("SC2BENCH_CONFIG must name an existing absolute configuration file")
        with path.open(encoding="utf-8") as file:
            config.read_file(file)
    return config


def get_version() -> tuple[str, str]:
    from sc2bench_env import __version__
    return __version__, "SC2Bench"


def register_sharpy_config() -> None:
    """Configure the maintained library through its explicit host-provider API."""
    from sharpy.runtime_config import configure
    configure(get_config, get_version)

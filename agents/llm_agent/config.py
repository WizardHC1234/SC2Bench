"""Configuration helpers for the example LLM agent."""

from __future__ import annotations

import argparse
import math
import os


DEFAULT_API_KEY = "EMPTY"
DEFAULT_API_BASE_URL = "http://172.18.132.20:2325/v1"
DEFAULT_MODEL = "DeepSeek-V4-Flash"
DEFAULT_TEMPERATURE = 0.5

SAFE_API_ERROR_TYPES = frozenset({
    "HTTPError", "URLError", "TimeoutError", "ConnectionError", "OSError",
    "SSLError", "RemoteDisconnected", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError", "IncompleteRead",
    "BadStatusLine", "HTTPException", "JSONDecodeError", "UnicodeDecodeError",
    "ValueError", "KeyError", "IndexError", "TypeError", "RuntimeError",
})


def _positive_number(value: str, *, integer: bool) -> int | float:
    try:
        number = int(value) if integer else float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive number") from error
    if integer:
        if type(number) is not int or number < 1:
            raise argparse.ArgumentTypeError("must be a positive integer")
        return number
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def positive_int(value: str) -> int:
    return int(_positive_number(value, integer=True))


def positive_float(value: str) -> float:
    return float(_positive_number(value, integer=False))


def parse_temperature(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("temperature must be >= 0")
    return number


def env_setting(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def llm_api_key() -> str:
    return env_setting("LLM_API_KEY", DEFAULT_API_KEY)


def llm_base_url() -> str:
    return env_setting("LLM_BASE_URL", DEFAULT_API_BASE_URL)


def llm_model() -> str:
    return env_setting("LLM_MODEL", DEFAULT_MODEL)

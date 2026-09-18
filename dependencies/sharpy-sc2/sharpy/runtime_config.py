"""Explicit host configuration; no generic config.py or working-directory lookup."""

from configparser import ConfigParser
from importlib import resources

_config_provider = None
_version_provider = None


def configure(config_provider, version_provider):
    if not callable(config_provider) or not callable(version_provider):
        raise TypeError("Sharpy runtime providers must be callable")
    global _config_provider, _version_provider
    _config_provider, _version_provider = config_provider, version_provider


def get_config(local=True):
    if _config_provider is not None:
        return _config_provider(local=local)
    config = ConfigParser()
    config.read_string(resources.files("sharpy").joinpath("default.ini").read_text(encoding="utf-8"))
    return config


def get_version():
    return _version_provider() if _version_provider is not None else ("0.1.0", "sharpy-sc2")

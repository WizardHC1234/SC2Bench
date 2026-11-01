"""Test Linux/Windows artifact selection without loading a foreign binary."""

import runpy
from pathlib import Path

import pytest


select_artifact = runpy.run_path(str(
    Path(__file__).resolve().parents[2] / "dependencies/sc2-pathlib/build_support.py"
))["select_artifact"]


def select(root, **overrides):
    options = dict(version=(3, 9, 25), implementation="CPython", system="Linux",
                   bits=64, extension_suffix=".cpython-39-x86_64-linux-gnu.so")
    options.update(overrides)
    return select_artifact(root, **options)


@pytest.mark.parametrize("system,suffix", [
    ("Linux", ".cpython-39-x86_64-linux-gnu.so"),
    ("Linux", ".cpython-39-aarch64-linux-gnu.so"),
    ("Windows", ".cp39-win_amd64.pyd"),
])
def test_matching_abi_artifact(tmp_path, system, suffix):
    package = tmp_path / "sc2pathlib"
    package.mkdir()
    native = package / ("sc2pathlib" + suffix)
    native.touch()
    assert select(tmp_path, system=system, extension_suffix=suffix) == native


def test_foreign_or_untagged_binary_does_not_satisfy_linux_build(tmp_path):
    package = tmp_path / "sc2pathlib"
    package.mkdir()
    for name in ("sc2pathlib.cp39-win_amd64.pyd", "sc2pathlib.so"):
        (package / name).touch()
    with pytest.raises(RuntimeError, match="Matching native artifact is missing"):
        select(tmp_path)


@pytest.mark.parametrize("overrides", [
    {"version": (3, 10)}, {"implementation": "PyPy"}, {"bits": 32},
    {"system": "Darwin"}, {"extension_suffix": None},
    {"extension_suffix": ".cp39-win_amd64.pyd"},
])
def test_unsupported_build_profile_fails_explicitly(tmp_path, overrides):
    with pytest.raises(RuntimeError):
        select(tmp_path, **overrides)

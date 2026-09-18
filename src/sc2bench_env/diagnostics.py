"""Read-only installation checks; native imports run in an isolated process."""

from __future__ import annotations

import importlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Optional, Sequence

from sc2bench_env.paths import default_paths

_REPORT_MARKER = "SC2BENCH_DIAGNOSTICS="


def _check(name: str, status: str, detail: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **fields}


def _inside(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _probe_sharpy(map_names: Sequence[str]) -> list[dict[str, Any]]:
    """Child-process only: use the real backend's import policy, never launch SC2."""
    from sc2bench_env.backends.sharpy.backend import _PROJECT_ROOT, _ensure_runtime_paths

    checks = []
    try:
        _ensure_runtime_paths()
    except Exception as error:
        checks.append(_check("runtime_configuration", "error", "Maintained Sharpy configuration API could not be prepared.",
                             error_type=type(error).__name__, hint="Install the SC2Bench-maintained dependencies from this project."))
    commander = Path(_PROJECT_ROOT).parent / "SC2-Commander"
    fork = Path(_PROJECT_ROOT) / "dependencies" / "sharpy-sc2"
    for distribution in ("burnysc2", "numpy", "scipy", "scikit-learn", "sharpy-sc2", "sc2bench-pathlib", "protobuf", "jsonpickle", "six"):
        try:
            version = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            checks.append(_check("package:" + distribution, "error",
                                 "Distribution metadata not installed.",
                                 hint="Install the tested Sharpy dependencies and maintained fork; see docs/installation.md."))
        else:
            checks.append(_check("package:" + distribution, "pass", "Installed.", version=version))

    for name in ("sc2", "sharpy", "sc2bench_env.backends.sharpy.runtime_config", "sc2pathlib", "jsonpickle"):
        try:
            module = importlib.import_module(name)
            origin = str(Path(module.__file__).resolve())
            legacy = _inside(origin, commander)
            fields = {"origin": origin, "legacy_dependency": legacy}
            if name == "sc2bench_env.backends.sharpy.runtime_config":
                config = module.get_config()
                for key in ("chat", "debug", "log_file", "write_data", "write_gamelogs"):
                    config.getboolean("general", key)
                config.getint("general", "game_step_size")
                config.get("general", "log_level")
                config.getboolean("debug", "player1")
                config.getboolean("debug", "player2")
            if name == "sharpy":
                compat = importlib.import_module("sharpy.sc2_compat")
                if not callable(getattr(compat, "patch_unknown_ability_orders", None)):
                    raise ImportError("Missing maintained fork compatibility API")
                fields["expected_local_fork"] = _inside(origin, fork)
            if name == "sc2pathlib":
                import numpy as np
                native = importlib.import_module("sc2pathlib.sc2pathlib")
                finder = module.PathFinder(np.ones((5, 5), dtype=np.int32))
                if finder.width != 5 or finder.height != 5:
                    raise RuntimeError("Unexpected PathFinder dimensions")
                path, distance = finder.find_path((0, 0), (4, 4))
                if not path or tuple(path[-1]) != (4, 4) or distance <= 0:
                    raise RuntimeError("Native path lookup failed")
                for attribute in ("Sc2Map", "MapType"):
                    getattr(module, attribute)
                native_origin = str(Path(native.__file__).resolve())
                fields["implementation_origin"] = native_origin
                compiled = any(native_origin.lower().endswith(extension) for extension in (".pyd", ".so"))
                fields["compiled"] = compiled
                if not compiled:
                    checks.append(_check("native_implementation", "warning",
                                         "Python fallback loaded; native performance is not established.", origin=native_origin))
            checks.append(_check("import:" + name, "warning" if legacy else "pass",
                                 "Loaded from SC2-Commander; this is not a standalone installation." if legacy
                                 else "Import and required API check passed.", **fields))
        except Exception as error:
            # Arbitrary import exceptions may contain credentials; report only their type.
            checks.append(_check("import:" + name, "error", "Import or required API check failed.",
                                 error_type=type(error).__name__,
                                 hint="Check the module origin, Python/native ABI and configuration; see docs/installation.md."))

    try:
        from sc2bench_env.backends.sharpy.compat import register_modern_terran_abilities
        register_modern_terran_abilities()
        from sc2bench_env.backends.sharpy.bot import BenchBot
        from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
        assert callable(BenchBot) and callable(TerranAdapter)
        checks.append(_check("terran_backend_imports", "pass", "Bot and Terran adapter imports passed (no game started)."))
    except Exception as error:
        checks.append(_check("terran_backend_imports", "error", "Bot or Terran adapter import failed.",
                             error_type=type(error).__name__, hint="Install the maintained Sharpy fork, not an unrelated upstream release."))

    try:
        from sc2.paths import Paths
        executable = Path(Paths.EXECUTABLE)
        if not executable.is_file():
            raise FileNotFoundError
        checks.append(_check("sc2_client", "pass", "Client executable exists; launchability has not been tested.",
                             executable=str(executable), base_directory=str(Paths.BASE)))
    except (Exception, SystemExit) as error:
        # BurnySC2's lazy Paths resolver calls exit() when no client is installed.
        checks.append(_check("sc2_client", "error", "Client executable could not be resolved.",
                             error_type=type(error).__name__, hint="Install SC2 and set SC2PATH to its installation directory if necessary."))
        checks.extend(_check("map:" + name, "error", "Not checked: SC2 client paths are unavailable.") for name in map_names)
    else:
        for name in map_names:
            try:
                from sc2 import maps
                game_map = maps.get(name)
                if not game_map.path.is_file():
                    raise FileNotFoundError
                checks.append(_check("map:" + name, "pass", "Map file resolves with the backend's map lookup.",
                                     path=str(game_map.path.resolve())))
            except Exception as error:
                checks.append(_check("map:" + name, "error", "Map lookup failed.", error_type=type(error).__name__,
                                     hint="Place the matching .SC2Map in the SC2 Maps directory (or a first-level subdirectory)."))
    return checks


def inspect_installation(*, backend: str = "sharpy", map_names: Optional[Sequence[str]] = None,
                         timeout_seconds: float = 45) -> dict[str, Any]:
    """Return preflight facts, not a guarantee of successful games or standalone packaging."""
    if backend not in {"fake", "sharpy"}:
        raise ValueError("Unsupported backend")
    names = list(dict.fromkeys(map_names if map_names is not None else ["KairosJunctionLE"]))
    if not all(isinstance(name, str) and name.strip() for name in names):
        raise ValueError("Map names must be nonempty strings")
    checks = [_check("python", "pass" if sys.version_info >= (3, 9) else "error",
                     "SC2Bench requires Python >= 3.9; the tested live profile is Windows Python 3.9.",
                     version=platform.python_version(), executable=sys.executable)]
    try:
        paths = default_paths()
        checks.append(_check("output_paths", "pass", "Default paths resolved without creating directories.", paths=paths))
    except Exception as error:
        checks.append(_check("output_paths", "error", "Output root configuration is invalid.",
                             error_type=type(error).__name__, hint="SC2BENCH_OUTPUT_DIR must be a nonempty absolute path."))
    if backend == "fake":
        from sc2bench_env.backends.fake import FakeBackend
        checks.append(_check("fake_backend_import", "pass", "FakeBackend imported; SC2, maps and Sharpy are not required."))
    else:
        code = ("import json,sys; from sc2bench_env.diagnostics import _probe_sharpy,_REPORT_MARKER; "
                "print(_REPORT_MARKER+json.dumps(_probe_sharpy(json.loads(sys.argv[1]))))")
        try:
            result = subprocess.run([sys.executable, "-c", code, json.dumps(names)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout_seconds,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            if result.returncode != 0:
                checks.append(_check("sharpy_probe", "error", "Isolated import probe exited unexpectedly.",
                                     exit_code=result.returncode, hint="Check native Python/architecture compatibility; no game was launched."))
            else:
                payload = next(line[len(_REPORT_MARKER):] for line in reversed(result.stdout.splitlines())
                               if line.startswith(_REPORT_MARKER))
                child_checks = json.loads(payload)
                if not isinstance(child_checks, list) or not all(
                    isinstance(item, dict) and item.get("status") in {"pass", "warning", "error"}
                    and isinstance(item.get("name"), str) and isinstance(item.get("detail"), str)
                    for item in child_checks
                ):
                    raise ValueError("Invalid probe report")
                checks.extend(child_checks)
        except subprocess.TimeoutExpired:
            checks.append(_check("sharpy_probe", "error", "Isolated import probe timed out.",
                                 hint="Check blocked imports/native loading; no game was launched."))
        except Exception as error:
            checks.append(_check("sharpy_probe", "error", "Isolated import report could not be read.",
                                 error_type=type(error).__name__))
    errors = sum(item["status"] == "error" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    legacy = [item["name"] for item in checks if item.get("legacy_dependency")]
    return {"schema_version": "1", "backend": backend, "checks": checks,
            "status": "failed" if errors else "passed_with_warnings" if warnings else "passed",
            "error_count": errors, "warning_count": warnings, "legacy_dependencies": legacy,
            "scope": "Read-only preflight; no SC2 launch, Agent/model call, record creation or dependency installation."}


def format_report(report: dict[str, Any]) -> str:
    lines = ["SC2Bench installation check ({})".format(report["backend"])]
    for item in report["checks"]:
        lines.append("[{}] {}: {}".format(item["status"].upper(), item["name"], item["detail"]))
        for field in ("version", "origin", "implementation_origin", "executable", "path", "error_type", "hint"):
            if field in item:
                lines.append("  {}: {}".format(field, item[field]))
    lines.append("Result: {} ({} errors, {} warnings)".format(
        report["status"], report["error_count"], report["warning_count"]))
    lines.append(report["scope"])
    return "\n".join(lines)

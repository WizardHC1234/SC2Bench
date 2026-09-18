"""Runtime dependencies must not need sys.path injection or old projects."""
import os
import subprocess
import sys

from sc2bench_env.backends.sharpy.backend import _PROJECT_ROOT, _ensure_runtime_paths


def test_runtime_paths_are_unchanged_and_configuration_is_idempotent(monkeypatch):
    from sc2bench_env.backends.sharpy import runtime_config
    original = list(sys.path)
    monkeypatch.delitem(sys.modules, "config", raising=False)
    _ensure_runtime_paths()
    _ensure_runtime_paths()
    assert sys.path == original
    assert "config" not in sys.modules
    from sharpy.runtime_config import _config_provider
    assert _config_provider is runtime_config.get_config


def test_fresh_process_imports_independent_dependencies(tmp_path):
    import json
    fork = os.path.join(_PROJECT_ROOT, "dependencies", "sharpy-sc2")
    native = os.path.join(_PROJECT_ROOT, "dependencies", "sc2-pathlib")
    result = subprocess.run([
        sys.executable, "-c",
        "import json,os; "
        "from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths; "
        "before=os.getcwd(); _ensure_runtime_paths(); import sharpy,sc2pathlib; "
        "from sc2bench_env.backends.sharpy import runtime_config; "
        "from sc2bench_env.backends.sharpy.bot import BenchBot; "
        "print(json.dumps([sharpy.__file__,sc2pathlib.__file__,runtime_config.__file__,os.getcwd()==before]))",
    ], cwd=tmp_path, capture_output=True, text=True, check=True)
    origins = json.loads(result.stdout)
    assert os.path.normcase(os.path.abspath(origins[0])).startswith(os.path.normcase(fork) + os.sep)
    assert os.path.normcase(os.path.abspath(origins[1])).startswith(os.path.normcase(native) + os.sep)
    assert origins[2].endswith("runtime_config.py")
    assert origins[3] is True


def test_unknown_buff_does_not_hide_known_skill_effects():
    from types import SimpleNamespace
    from sc2.ids.buff_id import BuffId
    from sc2.unit import Unit
    from sharpy.sc2_compat import patch_unknown_ability_orders
    patch_unknown_ability_orders()
    unit = Unit(
        SimpleNamespace(tag=1, buff_ids=[BuffId.LOCKON.value, 999999]),
        SimpleNamespace(state=SimpleNamespace(game_loop=1)),
    )
    assert unit.buffs == {BuffId.LOCKON}

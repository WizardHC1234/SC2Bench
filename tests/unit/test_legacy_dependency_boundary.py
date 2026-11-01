"""No legacy project imports or path injection in shipped runtime sources."""

import ast
from pathlib import Path


def test_runtime_sources_have_no_legacy_imports():
    root = Path(__file__).resolve().parents[2]
    folders = [root / "src", root / "dependencies" / "sharpy-sc2" / "sharpy",
               root / "dependencies" / "sc2-pathlib" / "sc2pathlib"]
    forbidden = {"commander", "evol_agent", "evolution", "llm", "sc2agentbench", "config"}
    violations = []
    for folder in folders:
        for path in folder.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                if any(name.split(".")[0] in forbidden for name in names):
                    violations.append(str(path.relative_to(root)) + ":" + str(node.lineno))
    assert not violations, violations


def test_backend_does_not_change_cwd_or_sys_path():
    from sc2bench_env.backends.sharpy import backend
    path = Path(backend.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(name in {"os.chdir", "sys.path.insert", "sys.path.append", "sys.path.remove"} for name in calls)


def test_unit_tests_do_not_inject_legacy_dependency_paths():
    folder = Path(__file__).resolve().parent
    violations = []
    for path in folder.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {"sys.path.insert", "sys.path.append"}:
                violations.append(path.name + ":" + str(node.lineno))
    assert not violations, violations

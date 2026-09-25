"""Audit knowledge catalogs. Exit 1 when a gap is not on the allowlist."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sc2bench_env.data.knowledge import (  # noqa: E402
    _forms, _movement, _roles, bound_snapshot, names_for,
)
from sc2bench_env.interface.action_catalog import get_catalog, get_target  # noqa: E402
from sc2bench_env.interface.tools import query_unit_data  # noqa: E402


def _allowlist() -> set[str]:
    path = ROOT / "data" / "sc2" / "common" / "knowledge_allowlist.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["id"]) for item in payload.get("gaps") or [] if item.get("reason")}


def collect_issues() -> list[str]:
    issues: list[str] = []
    platform = json.loads((ROOT / "data" / "sc2" / "common" / "platform_behavior.json").read_text(encoding="utf-8"))
    for name, row in (platform.get("observable_only") or {}).items():
        if row.get("race") not in {"terran", "protoss", "zerg"}:
            issues.append(f"observable_missing_race:{name}")
    for race in ("terran", "protoss", "zerg"):
        foreign = {"archon", "broodling", "mule", "interceptor", "changeling"} - {
            row_name for row_name, row in (platform.get("observable_only") or {}).items()
            if row.get("race") == race
        }
        leaked = foreign.intersection(names_for(race, "unit"))
        if leaked:
            issues.append(f"cross_race:{race}:{','.join(sorted(leaked))}")
        catalog_names = {spec.name for spec in get_catalog(race=race).targets}
        for spec in get_catalog(race=race).targets:
            if spec.action not in {"train", "build", "research", "upgrade"}:
                continue
            if get_target(spec.name, race=race) is None:
                issues.append(f"unresolved_target:{race}:{spec.name}")
            for req in spec.prerequisites:
                if req not in catalog_names:
                    issues.append(f"missing_prerequisite:{race}:{spec.name}:{req}")
            if spec.action == "research":
                continue
            forms = _forms(race, spec.name)
            if not forms and spec.action in {"train", "build", "upgrade"}:
                issues.append(f"no_snapshot_form:{race}:{spec.name}")
            primary = (((json.loads((ROOT / "data" / "sc2" / "common" / "aliases.json").read_text(encoding="utf-8")).get("primary_forms") or {}).get(race) or {}).get(spec.name))
            if forms and not primary:
                issues.append(f"missing_primary:{race}:{spec.name}")
            for form in forms:
                layer = _movement(str(form.get("name") or ""), bool(form.get("is_structure")))
                if layer == "unknown":
                    issues.append(f"movement_unknown:{race}:{form.get('name')}")
            if _roles(spec.name, forms, race) == ["unknown"]:
                issues.append(f"roles_unknown:{race}:{spec.name}")
            attacks = []
            for form in forms:
                targets = {str(weapon.get("targets")) for weapon in form.get("weapons") or []}
                ground = "Ground" in targets or "Any" in targets
                air = "Air" in targets or "Any" in targets
                if ground and air:
                    attacks.append("ground_and_air")
                elif ground:
                    attacks.append("ground")
                elif air:
                    attacks.append("air")
                else:
                    attacks.append("none")
            if attacks and len(set(attacks)) > 1:
                row = query_unit_data([spec.name], race=race)["results"][0]
                if row.get("can_attack") != "varies_by_form":
                    issues.append(f"can_attack_mismatch:{race}:{spec.name}")
    for name, raw in (platform.get("unit_platform_behavior") or {}).items():
        code = raw.get("code") if isinstance(raw, dict) else ""
        if not code or not (ROOT / code).is_file():
            issues.append(f"platform_behavior_untraced:{name}")
        if isinstance(raw, dict) and not raw.get("reason"):
            issues.append(f"platform_behavior_no_reason:{name}")
    for group_name in ("food_provided_corrections",):
        for name, row in (platform.get(group_name) or {}).items():
            if not row.get("reason") or not row.get("version"):
                issues.append(f"correction_incomplete:{name}")
    for race, rows in (platform.get("action_cost_corrections") or {}).items():
        for name, row in rows.items():
            if not row.get("reason") or not row.get("version"):
                issues.append(f"correction_incomplete:{race}:{name}")
    snapshot = bound_snapshot()
    runtime_path = ROOT / "data" / "sc2" / "snapshots" / snapshot["folder"] / "runtime_stats.json"
    if not runtime_path.is_file():
        issues.append("runtime_stats_missing")
        return issues
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    observed = {str(row.get("proto_name") or "").upper() for row in (runtime.get("units") or {}).values()}
    explained = {
        str(item.get("proto_name") or "").upper()
        for item in runtime.get("unobserved") or []
        if item.get("reason")
    }
    primaries = json.loads((ROOT / "data" / "sc2" / "common" / "aliases.json").read_text(encoding="utf-8")).get("primary_forms") or {}
    for race, forms in primaries.items():
        for name, proto in (forms or {}).items():
            token = str(proto).upper()
            if token not in observed and token not in explained:
                issues.append(f"runtime_unobserved:{race}:{name}")
    return issues


def main() -> int:
    issues = collect_issues()
    allowed = _allowlist()
    unexpected = [item for item in issues if item not in allowed and not any(item.startswith(prefix) for prefix in ())]
    report = {"issues": issues, "unexpected": unexpected}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())

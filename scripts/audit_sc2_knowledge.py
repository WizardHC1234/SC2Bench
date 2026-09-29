"""Audit knowledge catalogs. Exit 1 when a gap is not on the allowlist."""
from __future__ import annotations


import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sc2bench_env.catalog.knowledge import (  # noqa: E402
    _forms, _movement, bound_snapshot,
)
from sc2bench_env.catalog.registry import get_catalog, get_target  # noqa: E402
from sc2bench_env.runtime.query_tools import catalog_roles as _roles, names_for  # noqa: E402
from sc2bench_env.runtime.query_tools import query_unit_data  # noqa: E402


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
            if spec.action not in {"train", "build", "research", "morph_townhall"}:
                continue
            if get_target(spec.name, race=race) is None:
                issues.append(f"unresolved_target:{race}:{spec.name}")
            for req in spec.prerequisites:
                if req not in catalog_names:
                    issues.append(f"missing_prerequisite:{race}:{spec.name}:{req}")
            if spec.action == "research":
                continue
            forms = _forms(race, spec.name)
            if not forms and spec.action in {"train", "build", "morph_townhall"}:
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


_LIVE_VERIFIED = {
    ("5.0.16.97563", "protoss", "warpgate", "build"),
    ("5.0.16.97563", "protoss", "warp_gate", "research"),
}


def coverage_rows() -> list[dict]:
    """One row per catalog target and snapshot.

    ``complete`` means the snapshot, catalog and query agree.
    ``live_verified`` is separate: Linux 4.10 was not started on this machine.
    """
    from sc2bench_env.catalog.knowledge import (
        game_data, require_snapshot_match, snapshot_has_ability, upgrade_row,
    )
    from sc2bench_env.catalog.registry import get_catalog

    rows = []
    for game_version, data_version, base_build in (
        ("5.0.16.97563", "F364D7C8BB1A0444ABC9BEE547B3FBB3", 97563),
        ("4.10.0.75689", "B89B5D6FA7CBF6452E721311BFBC6CB2", 75689),
    ):
        snapshot = require_snapshot_match(
            game_version=game_version,
            data_version=data_version,
            base_build=base_build,
        )
        with game_data(snapshot):
            runtime_path = ROOT / "data" / "sc2" / "snapshots" / snapshot["folder"] / "runtime_stats.json"
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            observed = {str(item.get("proto_name") or "").upper() for item in (runtime.get("units") or {}).values()}
            explained = {
                str(item.get("proto_name") or "").upper()
                for item in runtime.get("unobserved") or []
                if item.get("reason")
            }
            aliases = json.loads((ROOT / "data" / "sc2" / "common" / "aliases.json").read_text(encoding="utf-8"))
            primaries = aliases.get("primary_forms") or {}
            for race in ("terran", "protoss", "zerg"):
                for spec in get_catalog(race=race).targets:
                    if spec.action not in {"train", "build", "research", "morph_townhall"}:
                        continue
                    forms = _forms(race, spec.name)
                    proto = str(((primaries.get(race) or {}).get(spec.name) or "")).upper()
                    research_row = upgrade_row(race, spec.name) if spec.action == "research" else None
                    snapshot_present = bool(forms) or research_row is not None
                    runtime_present = (not proto) or proto in observed or proto in explained or spec.action == "research"
                    queried = get_target(spec.name, race=race)
                    exposed = queried is not None and (
                        spec.action != "research" or research_row is not None
                    ) and spec.executable
                    live = (game_version, race, spec.name, spec.action) in _LIVE_VERIFIED
                    differences = []
                    if not spec.executable:
                        status = "knowledge_only"
                    elif spec.action == "research" and research_row is None:
                        status = "version_not_applicable"
                    elif spec.action in {"train", "build", "morph_townhall"} and not forms:
                        status = "missing_field"
                        differences.append("no_snapshot_form")
                    elif not exposed:
                        status = "implementation_missing"
                    else:
                        status = "complete"
                        if not live:
                            differences.append("live_not_run")
                    if spec.name == "warpgate" and spec.action == "build":
                        paid = snapshot_has_ability("MorphBuildingGatewayWarpGateFree")
                        if paid and (queried.minerals, queried.vespene) != (25, 25):
                            status = "mismatch"
                            differences.append("first_conversion_cost")
                        if not paid and (queried.minerals, queried.vespene) != (0, 0):
                            status = "mismatch"
                            differences.append("linux_cost_must_stay_zero")
                    rows.append({
                        "version": game_version,
                        "race": race,
                        "target": spec.name,
                        "action": spec.action,
                        "catalog_present": True,
                        "snapshot_present": snapshot_present,
                        "runtime_stats_present": runtime_present,
                        "ability_present": snapshot_present,
                        "tool_query_ok": queried is not None,
                        "live_verified": live,
                        "differences": ",".join(differences),
                        "status": status,
                    })
    return rows


def main() -> int:
    if "--table" in sys.argv:
        rows = coverage_rows()
        print("\t".join([
            "version", "race", "target", "action", "catalog_present", "snapshot_present",
            "runtime_stats_present", "ability_present", "tool_query_ok", "live_verified",
            "differences", "status",
        ]))
        for row in rows:
            print("\t".join(str(row[key]) for key in (
                "version", "race", "target", "action", "catalog_present", "snapshot_present",
                "runtime_stats_present", "ability_present", "tool_query_ok", "live_verified",
                "differences", "status",
            )))
        return 0
    issues = collect_issues()
    allowed = _allowlist()
    unexpected = [item for item in issues if item not in allowed and not any(item.startswith(prefix) for prefix in ())]
    report = {"issues": issues, "unexpected": unexpected}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())

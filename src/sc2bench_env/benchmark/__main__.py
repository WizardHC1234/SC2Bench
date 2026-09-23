"""Platform entry point; external agents own their model calls and policy."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.paths import default_paths


def _agent_factory(reference: str) -> Callable:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute or ":" in attribute:
        raise ValueError("Agent must be an importable module:factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("Agent factory must be callable")
    return factory


def _positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SC2Bench external Agent execution and offline evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a Suite with an external Agent factory")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--agent", required=True, help="importable module:factory, called once per game")
    run.add_argument("--agent-metadata", type=Path, help="optional non-secret JSON configuration")
    run.add_argument("--backend", choices=("sharpy", "fake"), default="sharpy")
    run.add_argument("--record-dir", type=Path, default=None)
    run.add_argument("--max-parallel", type=_positive_int, default=1,
                     help="maximum simultaneous games; default 1 (serial)")
    commands.add_parser("paths", help="show unified default output paths without creating directories")
    doctor = commands.add_parser("doctor", help="read-only dependency/client/map preflight; never starts a game")
    doctor.add_argument("--backend", choices=("sharpy", "fake"), default="sharpy")
    source = doctor.add_mutually_exclusive_group()
    source.add_argument("--map", dest="map_names", action="append", help="map to check (repeatable); default KairosJunctionLE")
    source.add_argument("--suite", type=Path, help="check all distinct maps in a Suite")
    doctor.add_argument("--json", action="store_true", help="print structured check results")
    evaluate = commands.add_parser("evaluate", help="recompute facts without SC2 or model calls")
    evaluate.add_argument("batch", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            from sc2bench_env.diagnostics import format_report, inspect_installation
            names = args.map_names
            if args.suite is not None:
                names = [item["config"].map_name for item in BenchmarkSuite.load(args.suite).episode_plan()]
            report = inspect_installation(backend=args.backend, map_names=names)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
            return 1 if report["error_count"] else 0
        if args.command == "paths":
            print(json.dumps(default_paths(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "evaluate":
            print(json.dumps(Evaluator.evaluate_batch(args.batch), ensure_ascii=False, indent=2))
            return 0
        suite = BenchmarkSuite.load(args.suite)
        metadata: Any = None
        if args.agent_metadata is not None:
            metadata = json.loads(args.agent_metadata.read_text(encoding="utf-8-sig"))
        factory = _agent_factory(args.agent)
        batch = BenchmarkRunner(
            backend_factory=lambda: args.backend, record_dir=args.record_dir,
        ).run(suite, factory, agent_metadata=metadata, max_parallel=args.max_parallel)
        print(json.dumps({"status": batch["status"],
                          "aggregate": batch["aggregate"],
                          "termination_counts": batch["termination_counts"]},
                         ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # External modules and JSON errors can contain secrets in exception text.
        parser.exit(2, f"SC2Bench command failed ({type(error).__name__}); check configuration and imports.\n")


if __name__ == "__main__":
    raise SystemExit(main())

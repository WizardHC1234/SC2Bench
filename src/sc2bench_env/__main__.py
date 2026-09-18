"""Unified platform CLI, also exposed as the installed sc2bench command."""

from sc2bench_env.benchmark.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

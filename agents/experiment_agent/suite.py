"""Generate the agreed 360 scenarios without copying model/harness settings."""
from itertools import product

from sc2bench_env.benchmark import BenchmarkSuite

RACES = ("terran", "protoss", "zerg")
MAPS = ("AncientCisternLE", "AltitudeLE")
DIFFICULTIES = ("mediumhard", "hard", "harder", "veryhard")
STYLES = ("rush", "timing", "power", "macro", "air")


def build_suite(*, repetitions=20, max_decisions=None, game_time_limit_seconds=1800,
                game_seeds=None):
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("repetitions must be positive")
    if game_seeds is None:
        game_seeds = list(range(repetitions))
    return BenchmarkSuite.from_dict({
        "schema_version": "1", "suite_id": "sc2bench_full_matrix", "suite_version": "1",
        "purpose": "evaluation", "repetitions": repetitions, "max_decisions": max_decisions,
        "game_seeds": list(game_seeds),
        "episode_defaults": {"blocking_decisions": True, "realtime": False,
                             "game_time_limit_seconds": game_time_limit_seconds},
        "cases": [{
            "case_id": f"{own}_vs_{enemy}_{map_name}_{difficulty}_{style}",
            "episode": {"race": own, "enemy_race": enemy, "map_name": map_name,
                        "opponent": difficulty, "enemy_style": style},
        } for own, enemy, map_name, difficulty, style in product(RACES, RACES, MAPS, DIFFICULTIES, STYLES)],
    })

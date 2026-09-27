"""Geometry-only one-pass expansion ordering. No enemy inference."""

from typing import Sequence


def order_expansions(candidates: Sequence[tuple], origin: Sequence[float]):
    """Prefer fogged centers, then nearest next center.

    Candidates contain (zone_id, center_xy, center_visible), excluding own bases.
    Distance is straight-line ordering, not a promise of terrain reachability.
    """
    pending = list(candidates)
    position = origin
    route = []
    while pending:
        choice = min(pending, key=lambda item: (
            bool(item[2]),
            (item[1][0] - position[0]) ** 2 + (item[1][1] - position[1]) ** 2,
            item[0],
        ))
        pending.remove(choice)
        route.append(choice[0])
        position = choice[1]
    return route

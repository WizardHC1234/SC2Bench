"""Scout intent serialization and geometry-only one-pass route ordering."""

from typing import Optional, Sequence, Tuple, Union

ScoutRoute = Union[str, Tuple[str, ...]]


def route_payload(route: Optional[ScoutRoute]):
    return route if isinstance(route, str) else list(route or ())


def order_expansions(candidates: Sequence[tuple], origin: Sequence[float]):
    """Prefer fogged centers, then nearest next center; no enemy inference.

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

"""Static terrain-path information keyed by platform IDs, not Sharpy indices."""
import math


def read_topology(registry, zones):
    """Cache paths by coordinate identity before Sharpy reorders its zones.

    Corridor links use consecutive nearest expansion centers along terrain
    waypoints. They are a derived spatial graph, not tactical recommendations
    or a promise of current walkability through units/destructibles.
    """
    by_id = {}
    for zone in zones:
        center = zone.center_location
        zone_id = registry._key_to_id.get((round(float(center.x), 1), round(float(center.y), 1)))
        if zone_id is not None:
            by_id[zone_id] = zone
    ids = list(registry.zone_ids)
    centers = {name: registry.center_for(name) for name in ids}

    def distance(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    for i, first in enumerate(ids):
        for second in ids[i + 1:]:
            pair = (first, second)
            if pair in registry._topology_paths:
                continue
            a, b = by_id.get(first), by_id.get(second)
            if a is None or b is None:
                continue
            # The index is only a lookup into the current Sharpy path store.
            # Endpoints verify its coordinate identity before it is trusted.
            path = getattr(a, "paths", {}).get(getattr(b, "zone_index", None))
            if path is None:
                continue
            try:
                length = float(getattr(path, "distance", None))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(length) or length < 0:
                continue
            points = [(float(p[0]) + 0.5, float(p[1]) + 0.5)
                      for p in (getattr(path, "path", None) or [])]
            if not points:
                # Empty failed path stores have no endpoint identity after a
                # reorder. Do not turn missing/stale data into unreachable.
                continue
            matches = ((distance(points[0], centers[first]) <= 5
                        and distance(points[-1], centers[second]) <= 5)
                       or (distance(points[-1], centers[first]) <= 5
                           and distance(points[0], centers[second]) <= 5))
            if matches and length > 0:
                registry._topology_paths[pair] = (length, points)

    def path_distance(first, second):
        if first is None or second is None:
            return None
        if first == second:
            return 0.0
        pair = (first, second) if ids.index(first) < ids.index(second) else (second, first)
        cached = registry._topology_paths.get(pair)
        return cached[0] if cached is not None else None

    signature = (tuple(ids), len(registry._topology_paths))
    if registry._topology_signature != signature:
        links = {name: set() for name in ids}
        for _pair, (_length, points) in registry._topology_paths.items():
            previous = None
            for point in points:
                nearest = min(ids, key=lambda name: distance(point, centers[name]))
                if previous is not None and nearest != previous:
                    # A derived link must itself have a verified terrain path.
                    if path_distance(previous, nearest) is not None:
                        links[previous].add(nearest)
                        links[nearest].add(previous)
                previous = nearest
        registry._topology_links = links
        registry._topology_signature = signature
    links = registry._topology_links
    main = next((name for name in ids if registry.role_for_center(*centers[name]) == "own_main"), None)
    enemy = next((name for name in ids if registry.role_for_center(*centers[name]) == "enemy_main"), None)
    rows = []
    for name in ids:
        zone = by_id.get(name)
        rows.append({
            "zone_id": name,
            "has_ramp": zone.ramp is not None if zone is not None and hasattr(zone, "ramp") else None,
            "path_distance_from_own_main": path_distance(main, name),
            "path_distance_to_enemy_main": path_distance(name, enemy),
            "corridor_neighbors": [
                {"zone_id": other, "path_distance": path_distance(name, other)}
                for other in ids if other in links[name]
            ] or None,
        })
    return {"distance_basis": "static_terrain_paths",
            "neighbor_basis": "nearest_expansion_centers_along_verified_paths",
            "verified_path_pair_count": len(registry._topology_paths),
            "total_path_pair_count": len(ids) * (len(ids) - 1) // 2,
            "zones": rows}

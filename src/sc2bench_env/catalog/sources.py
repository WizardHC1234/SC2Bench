"""Source families for unit morphs and merges, in backend selection order."""


def unit_source_names(spec, *, race):
    if spec is None or spec.mechanism not in {"unit_morph", "merge"}:
        return ()
    if race == "protoss" and spec.name == "archon":
        return ("high_templar", "dark_templar")
    return (spec.produced_at,) if spec.produced_at else ()


def allocate_unit_sources(spec, count, available, *, race):
    """Allocate existing sources only; never invent stock to cover a shortage."""
    needed = max(0, int(count)) * (2 if spec.mechanism == "merge" else 1)
    found = {}
    for name in unit_source_names(spec, race=race):
        take = min(needed, max(0, int(available.get(name, 0))))
        if take:
            found[name] = take
            needed -= take
    return found

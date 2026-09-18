"""Per-bot console noise filtering, without changing Sharpy state tracking."""
import logging

from sharpy.managers.core import LogManager


class BenchLogManager(LogManager):
    """Keep warnings/errors, suppress routine unit discovery/loss notices."""

    quiet_tags = frozenset({"EnemyUnitsManager", "LostUnitsManager"})

    def print(self, message, tag=None, stats=True, log_level=logging.INFO):
        if tag in self.quiet_tags and log_level < logging.WARNING:
            return
        return super().print(message, tag=tag, stats=stats, log_level=log_level)

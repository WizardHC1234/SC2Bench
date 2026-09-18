"""Temporal acceptance helpers for isolated engine-observation scenarios.

Not platform combat credit: attribution is only valid with one attacker and
controlled targets. No cast proposals, mission flags, or cumulative gun damage.
"""
from dataclasses import dataclass, field
from math import hypot


@dataclass
class ObservedImpact:
    minimum_burst: float
    window_seconds: float = 5.0
    previous: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    hits: set = field(default_factory=set)

    def sample(self, now, health, ordered_targets=()):
        for tag in ordered_targets:
            if tag in health:
                self.pending[tag] = now + self.window_seconds
        for tag, hp in health.items():
            before = self.previous.get(tag)
            if (before is not None and 0 < now - before[0] <= 1.0
                    and now <= self.pending.get(tag, -1)
                    and before[1] - hp >= self.minimum_burst):
                self.hits.add(tag)
        # A disappeared target is not assumed to have been hit or killed.
        self.pending = {tag: expiry for tag, expiry in self.pending.items()
                        if tag in health and expiry >= now}
        self.previous = {tag: (now, hp) for tag, hp in health.items()}


@dataclass
class ObservedSustainedLock:
    minimum_seconds: float = 3.0
    first: dict = field(default_factory=dict)
    sustained: set = field(default_factory=set)

    def sample(self, now, health, locked_tags):
        current = set(locked_tags) & set(health)
        self.first = {tag: row for tag, row in self.first.items() if tag in current}
        for tag in current:
            since, initial_hp, samples, last = self.first.get(tag, (now, health[tag], 0, now))
            if now - last > 1.0:
                since, initial_hp, samples = now, health[tag], 0
            samples += 1
            self.first[tag] = (since, initial_hp, samples, now)
            if now - since >= self.minimum_seconds and samples >= 3 and initial_hp - health[tag] >= 20:
                self.sustained.add(tag)


@dataclass
class ObservedFormRoundTrip:
    base_form: object
    deployed_form: object
    tag: object = None
    stage: int = 0

    def sample(self, tag, form):
        if self.tag is None:
            if form != self.base_form:
                return
            self.tag = tag
        if tag != self.tag:
            return
        if self.stage == 0 and form == self.deployed_form:
            self.stage = 1
        elif self.stage == 1 and form == self.base_form:
            self.stage = 2


@dataclass
class ObservedTeleport:
    home: tuple
    previous: object = None
    pending: object = None
    arrived: bool = False

    @staticmethod
    def distance(a, b):
        return hypot(a[0] - b[0], a[1] - b[1])

    def sample(self, now, tag, position, destination=None):
        if destination is not None and self.distance(position, self.home) >= 24:
            self.pending = (tag, destination, now + 8)
        if self.pending is not None and self.previous is not None:
            old_time, old_tag, old_position = self.previous
            pending_tag, target, expiry = self.pending
            if (tag == old_tag == pending_tag and now <= expiry
                    and 0 < now - old_time <= 1
                    and self.distance(old_position, position) >= 20
                    and self.distance(position, target) <= 3
                    and self.distance(position, self.home) <= 12):
                self.arrived = True
        self.previous = (now, tag, position)


@dataclass
class ObservedLockCycle:
    """Current-client 14s cycle, allowing <=1s sampling uncertainty.

    Completion requires the SAME visible, living target to lose its buff
    naturally after >=13s continuous observation and actual damage.
    """
    first: object = None
    completed: bool = False

    def sample(self, now, health, locked_tags):
        locked = set(locked_tags) & set(health)
        if self.first is None:
            if locked:
                tag = next(iter(locked))
                self.first = (tag, now, now, health[tag])
            return
        tag, start, last, initial_hp = self.first
        if tag not in health or now - last > 1:
            self.first = None
            return
        if tag in locked:
            self.first = (tag, start, now, initial_hp)
        else:
            if last - start >= 13 and health[tag] <= initial_hp - 20:
                self.completed = True
            self.first = None

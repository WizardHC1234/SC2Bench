"""Unit tests for combat style geometry helpers and own-forces split."""
from __future__ import annotations


import pytest
from sc2.position import Point2

from sc2bench_env.backends.sharpy.combat_styles import (
    transport_drop_point,
    defend_leash_radius,
    style_move_type_name,
)
from sc2bench_env.interface.observations import split_own_forces


def test_attack_and_defend_use_combat_micro() -> None:
    assert style_move_type_name("attack") == "Assault"
    assert style_move_type_name("defend") == "Assault"


def test_transport_drop_point_stands_off_from_zone() -> None:
    zone = Point2((100.0, 100.0))
    home = Point2((10.0, 10.0))
    hold = transport_drop_point(zone, home, standoff=18.0)
    assert hold.distance_to(zone) == pytest.approx(18.0, abs=0.2)
    assert hold.distance_to(home) < zone.distance_to(home)


def test_defend_leash_scales_with_zone_radius() -> None:
    assert defend_leash_radius(12.0, leash_factor=1.25) == pytest.approx(15.0)
    assert defend_leash_radius(4.0, leash_factor=1.25) == 8.0


def test_own_forces_assigned_and_free() -> None:
    view = split_own_forces(
        {"scv": 12, "marine": 10, "marauder": 2},
        assigned={"marine": 4},
    )
    assert view.army["marine"] == 10
    assert view.assigned["marine"] == 4
    assert view.free["marine"] == 6
    assert view.free["marauder"] == 2
    assert "scv" not in view.assigned
    payload = view.to_dict()
    assert payload["army"]["marine"] == 10
    assert "total" not in payload
    assert payload["free"]["marine"] == 6


def test_transport_short_distance_does_not_overshoot_home() -> None:
    zone, home = Point2((5, 0)), Point2((0, 0))
    assert transport_drop_point(zone, home) == home
    assert transport_drop_point(zone, home, standoff=-1) == zone


def test_transport_standoff_is_not_a_containment_perimeter() -> None:
    zone, home = Point2((100, 0)), Point2((0, 0))
    assert transport_drop_point(zone, home) == Point2((92, 0))


def test_internal_combat_progress_never_exposes_task_ids() -> None:
    from sc2bench_env.interface.observations import Observation

    payload = Observation().to_dict()
    assert "info" not in payload
    assert "combat_progress" not in str(payload)

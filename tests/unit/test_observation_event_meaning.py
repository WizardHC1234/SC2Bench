"""Clarify actual recorded field meanings without changing event payloads."""
from sc2bench_env.interface.observation_text import event_lines, resource_lines


def test_completed_training_count_is_a_request_count_not_a_turn_delta():
    event = {"type": "train_completed", "target": "marine", "count": 4}
    text = "\n".join(event_lines([event]))
    assert "Count: 4" in text
    assert event == {"type": "train_completed", "target": "marine", "count": 4}


def test_withdrawn_group_composition_is_not_presented_as_losses():
    event = {"type": "combat_ended", "units": {"medivac": 4}, "end_reason": "withdrawn"}
    text = "\n".join(event_lines([event]))
    assert "Units: medivac 4" in text and "End reason: withdrawn" in text
    assert "loss" not in text.lower()


def test_deposits_and_own_gas_structures_are_explicit():
    text = "\n".join(resource_lines([{"zone_id": "zone_15", "minerals_remaining": 2690,
        "vespene_remaining": 1752, "owned_gas_structure_count": 0, "available_geyser_slots": 0}]))
    assert text.startswith("Base resources:")
    assert "gas structures 0" in text
